#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
=============================================================================
 flight_monitor.py  -  VERSAO STANDALONE
=============================================================================
 Mesmo pipeline do projeto completo, mas em UM arquivo e usando SOMENTE a
 biblioteca padrao do Python. Nada de pip install, nada de venv, nada de
 download de pacote. Funciona no Python 3.9+ (testado ate 3.14).

 COMO USAR (Windows, CMD):
     cd /d C:\Users\SEU_USUARIO\Desktop
     python flight_monitor.py demo
     python flight_monitor.py init-db
     python flight_monitor.py seed
     python flight_monitor.py scan
     python flight_monitor.py scan --watch-id <ID>

 Se "python" abrir a Microsoft Store, use "py -3" no lugar de "python".

 VARIAVEIS DE AMBIENTE (opcionais, todas tem padrao):
     FDM_DB        caminho do SQLite      (padrao: ./data/monitor.db)
     FDM_PCT       porcentagem de alerta  (padrao: 50)
     FDM_ORIGEM    IATA de origem         (padrao: GRU)
     FDM_DESTINO   IATA de destino        (padrao: LAX)
     FDM_INICIO    data ida  DD-MM-YYYY   (padrao: 01-10-2026)
     FDM_FIM       data volta DD-MM-YYYY  (padrao: 31-12-2026)
     FDM_PYUTF8    "0" desliga o modo UTF-8 do console

 NOTA: usa fontes SINTETICAS (gerador deterministico) para que voce valide
 toda a logica sem credenciais. Os adaptadores reais (Amadeus, Skyscanner,
 Kiwi) estao no projeto completo, em src/sources/.
=============================================================================
"""
from __future__ import annotations

import argparse
import hashlib
import math
import os
import random
import sqlite3
import sys
import uuid
from urllib.parse import quote
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Console seguro no Windows (cp1252/cp850 nao desenham emoji/acentos)
# ---------------------------------------------------------------------------
if os.environ.get("FDM_PYUTF8", "1") != "0":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------
BASE_CURRENCY = "BRL"
FX = {"BRL": 1.00, "USD": 5.40, "EUR": 5.85, "GBP": 6.90, "ARS": 0.0055, "CLP": 0.0057}

RELIABILITY = {"mock_a": 0.80, "mock_b": 0.70, "mock_c": 0.60}
HALF_LIFE_H = 72.0
MIN_SAMPLES = 5
IQR_MULT = 1.5
MAX_AGE_H = 168.0
MAX_ALERTS_DAY = 3

DB_PATH = os.environ.get("FDM_DB", str(Path("data") / "monitor.db"))

# AEROPORTOS BRASILEIROS (rota domestica: preco, duracao e executiva diferentes)
BR_AIRPORTS = {"GRU","CGH","VCP","SDU","GIG","BSB","CNF","CWB","FLN","POA","SSA","REC","FOR",
    "NAT","JPA","MCZ","AJU","SLZ","THE","MAO","BEL","PVH","CGB","GYN","VIX","IGU","NVT",
    "JOI","MGF","LDB","RAO","UDI","IOS","PLU","PNZ","FEN","CPV"}

# Preco tipico de referencia por rota (IDA E VOLTA, economica, BRL)
BASE_PRICE = {
    "GRU-CGH": 500, "GRU-VCP": 500, "GRU-SDU": 800, "GRU-CNF": 700, "GRU-CWB": 800,
    "GRU-FLN": 900, "GRU-POA": 1000, "GRU-VIX": 1050, "GRU-GYN": 800, "GRU-BSB": 900,
    "GRU-IGU": 950, "GRU-CGB": 1250, "GRU-SSA": 1300, "GRU-REC": 1400, "GRU-MCZ": 1500,
    "GRU-AJU": 1450, "GRU-JPA": 1550, "GRU-NAT": 1650, "GRU-FOR": 1550, "GRU-SLZ": 1600,
    "GRU-THE": 1500, "GRU-IOS": 1200, "GRU-MAO": 1900, "GRU-BEL": 1800, "GRU-PVH": 2000,
    "GRU-EZE": 1500, "GRU-MVD": 1400, "GRU-SCL": 1900, "GRU-ASU": 1600, "GRU-LIM": 2100,
    "GRU-BOG": 2300, "GRU-PTY": 2700, "GRU-MEX": 3200, "GRU-MIA": 2900, "GRU-JFK": 3600,
    "GRU-LAX": 4200, "GRU-MAD": 3900, "GRU-LIS": 3800, "GRU-OPO": 3800, "GRU-CDG": 4100,
    "GRU-FCO": 4300, "GRU-AMS": 4200, "GRU-BCN": 4000, "GRU-LHR": 4400, "GRU-FRA": 4200,
    "GRU-ZRH": 4300, "GRU-NRT": 6800,
}
CABIN_MULT = {"economy": 1.0, "business": 2.85}
ONEWAY_FACTOR = 0.58      # so-ida ~ 58% do valor de ida e volta


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_ddmmyyyy(v: str) -> date:
    v = v.strip()
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    raise ValueError("Data invalida %r: use DD-MM-YYYY (ex.: 01-10-2026)" % v)


def brl(v: float) -> str:
    return "R$ {:,.2f}".format(v).replace(",", "X").replace(".", ",").replace("X", ".")


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------
@dataclass
class Fare:
    source: str
    origin: str
    destination: str
    departure_date: date
    return_date: date | None
    cabin_class: str
    fare_total: float          # em BRL
    currency_original: str
    fare_original: float
    fx_rate: float
    taxes: float = 0.0
    fees: float = 0.0
    timestamp: datetime = field(default_factory=now_utc)
    refundable: bool = False
    duration_minutes: int = 0
    stops: int = 0
    baggage_included: bool = False
    airline: str | None = None
    deep_link: str | None = None

    @property
    def route(self) -> str:
        return "%s-%s" % (self.origin, self.destination)

    @property
    def route_key(self) -> str:
        return "%s-%s:%s:%s:%s" % (
            self.origin, self.destination,
            self.departure_date.isoformat(),
            self.return_date.isoformat() if self.return_date else "",
            self.cabin_class,
        )

    @property
    def age_hours(self) -> float:
        return (now_utc() - self.timestamp).total_seconds() / 3600.0

    def stable_hash(self) -> str:
        raw = "|".join([self.source, self.origin, self.destination,
                        self.departure_date.isoformat(),
                        self.return_date.isoformat() if self.return_date else "",
                        self.cabin_class, str(self.stops),
                        str(self.duration_minutes), self.airline or ""])
        return hashlib.sha1(raw.encode()).hexdigest()[:16]


@dataclass
class MarketValue:
    route_key: str
    cabin_class: str
    valor_de_mercado: float
    desvio_padrao: float
    p25: float
    p75: float
    iqr: float
    min_fare: float
    max_fare: float
    n_samples: int
    n_outliers: int
    confidence: float
    method: str = "weighted_mean_iqr"
    computed_at: datetime = field(default_factory=now_utc)


@dataclass
class Alert:
    id: str
    watch_id: str
    route: str
    cabin_class: str
    preco_atual: float
    valor_de_mercado: float
    porcentagem_configurada: float
    desconto_real_pct: float
    score: float
    source: str
    departure_date: date
    return_date: date | None
    deep_link: str | None
    channels: list
    reason: str
    created_at: datetime = field(default_factory=now_utc)


# ---------------------------------------------------------------------------
# Fontes sinteticas (deterministicas por rota/data/cabine)
# ---------------------------------------------------------------------------
class MockSource:
    def __init__(self, name, currency, bias, noise, flash_chance, flash_min, flash_max):
        self.name = name
        self.currency = currency
        self.bias = bias
        self.noise = noise
        self.flash_chance = flash_chance
        self.flash_min = flash_min
        self.flash_max = flash_max

    def search(self, origin, destination, dep, ret, cabin, oneway=False):
        key = "%s-%s" % (origin, destination)
        dom = origin in BR_AIRPORTS and destination in BR_AIRPORTS
        b0 = (BASE_PRICE.get(key)
              or BASE_PRICE.get("%s-%s" % (destination, origin))
              or (900.0 if dom else 3500.0))
        cabm = 2.1 if (cabin == "business" and dom) else CABIN_MULT[cabin]
        base = b0 * cabm * (ONEWAY_FACTOR if oneway else 1.0)
        seed = int(hashlib.sha1("|".join(
            [self.name, origin, destination, dep.isoformat(),
             ret.isoformat() if ret else "", cabin,
             "OW" if oneway else "RT"]).encode()).hexdigest()[:12], 16)
        rng = random.Random(seed)
        rate = FX.get(self.currency, 1.0)
        out = []
        for i in range(rng.randint(6, 14)):
            weekend = 1.12 if dep.weekday() >= 5 else 1.0
            price = base * weekend * self.bias * rng.lognormvariate(0, self.noise)
            if rng.random() < 0.08:
                price *= 1 - rng.uniform(0.15, 0.42)
            flash = i == 0 and rng.random() < self.flash_chance
            if flash:
                price = base * rng.uniform(self.flash_min, self.flash_max)
            stops = rng.choices([0, 1, 2], weights=([60, 32, 8] if dom else [35, 45, 20]))[0]
            if dom:
                dur = int(rng.uniform(110, 290))
            else:
                dur = int(rng.uniform(540, 1020))
            if stops:
                dur += stops * rng.randint(90, 240)
            local = round(price / rate, 2)
            out.append(Fare(
                source=self.name, origin=origin, destination=destination,
                departure_date=dep, return_date=ret, cabin_class=cabin,
                fare_total=round(price, 2), currency_original=self.currency,
                fare_original=local, fx_rate=rate,
                taxes=round(price * 0.16, 2), fees=round(price * 0.03, 2),
                refundable=rng.random() < (0.4 if cabin == "business" else 0.12),
                duration_minutes=dur, stops=stops,
                baggage_included=rng.random() < (0.85 if cabin == "business" else 0.45),
                airline=rng.choice(["LA", "JJ", "AA", "IB", "TP", "AF", "UA", "CM"]),
                deep_link="https://mock.example.com/%s/%s%s/%s" % (self.name, origin, destination, dep),
            ))
        return out


def build_sources():
    return [
        MockSource("mock_a", "BRL", 1.00, 0.09, 1.0, 0.45, 0.60),
        MockSource("mock_b", "USD", 1.04, 0.13, 1.0, 0.45, 0.60),
        MockSource("mock_c", "EUR", 0.97, 0.18, 0.5, 0.38, 0.55),
    ]


# ---------------------------------------------------------------------------
# Normalizacao
# ---------------------------------------------------------------------------
def sanitize(f: Fare) -> Fare | None:
    if not (80.0 <= f.fare_total <= 250000.0):
        return None
    if f.return_date and f.return_date < f.departure_date:
        return None
    if f.departure_date < date.today():
        return None
    if f.duration_minutes and f.duration_minutes < 25:
        return None
    if f.stops >= 6:
        return None
    return f


def normalize_many(fares):
    best = {}
    for f in fares:
        f = sanitize(f)
        if f is None:
            continue
        h = f.stable_hash()
        if h not in best or f.fare_total < best[h].fare_total:
            best[h] = f
    return sorted(best.values(), key=lambda x: x.fare_total)


# ---------------------------------------------------------------------------
# Estatistica robusta (puro Python)
# ---------------------------------------------------------------------------
def _interp(xs, ys, q):
    if q <= xs[0]:
        return ys[0]
    if q >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if q <= xs[i]:
            if xs[i] == xs[i - 1]:
                return ys[i]
            t = (q - xs[i - 1]) / (xs[i] - xs[i - 1])
            return ys[i - 1] + t * (ys[i] - ys[i - 1])
    return ys[-1]


def wquantile(pairs, q):
    """Quantil ponderado (mesma convencao do numpy: (cumsum - w/2)/total)."""
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs) or 1.0
    xs, ys, acc = [], [], 0.0
    for v, w in pairs:
        xs.append((acc + w / 2.0) / total)
        ys.append(v)
        acc += w
    return _interp(xs, ys, q)


def wmean(pairs):
    tw = sum(w for _, w in pairs) or 1.0
    return sum(v * w for v, w in pairs) / tw


def wstd(pairs, mean):
    if len(pairs) < 2:
        return 0.0
    tw = sum(w for _, w in pairs) or 1.0
    return math.sqrt(max(0.0, sum(w * (v - mean) ** 2 for v, w in pairs) / tw))


def median(vals):
    s = sorted(vals)
    n = len(s)
    if not n:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def mad_sigma(vals):
    if len(vals) < 2:
        return 0.0
    m = median(vals)
    return 1.4826 * median([abs(v - m) for v in vals])


def sample_weight(f: Fare) -> float:
    rel = RELIABILITY.get(f.source, 0.5)
    decay = 0.5 ** (max(f.age_hours, 0.0) / HALF_LIFE_H)
    comp = 1.0
    if f.duration_minutes <= 0:
        comp *= 0.85
    if f.taxes <= 0 and f.fees <= 0:
        comp *= 0.9
    return max(rel * decay * comp, 1e-4)


def service_factor(f: Fare, ref_dur: float) -> float:
    factor = 1.0
    if ref_dur > 0 and f.duration_minutes > 0:
        excess = max(0.0, (f.duration_minutes - ref_dur) / ref_dur)
        factor += min(0.30, 0.10 * math.log1p(excess * 10) / math.log1p(10) * 3)
    factor += min(0.18, 0.06 * f.stops)
    if not f.baggage_included:
        factor += 0.05
    if f.refundable:
        factor -= 0.02
    return max(0.85, min(1.60, factor))


def compute_market_value(fares, cabin, route_key):
    fresh = [f for f in fares if f.age_hours <= MAX_AGE_H]
    if not fresh:
        return None, []
    # mesmo voo coletado mais de uma vez: mantem o menor preco
    best = {}
    for f in fresh:
        h = f.stable_hash()
        if h not in best or f.fare_total < best[h].fare_total:
            best[h] = f
    fresh = list(best.values())
    pairs = [(f.fare_total, sample_weight(f)) for f in fresh]
    n0 = len(pairs)

    # poda UNICA por IQR (teto de 25% da amostra)
    removed, lo, hi = 0, min(p[0] for p in pairs), max(p[0] for p in pairs)
    if n0 >= MIN_SAMPLES:
        q1 = wquantile(pairs, 0.25)
        q3 = wquantile(pairs, 0.75)
        iqr = q3 - q1
        if iqr > 0:
            lo, hi = q1 - IQR_MULT * iqr, q3 + IQR_MULT * iqr
            keep = [p for p in pairs if lo <= p[0] <= hi]
            max_rm = int(n0 * 0.25)
            if n0 - len(keep) > max_rm:
                c = wmean(pairs)
                keep = sorted(pairs, key=lambda p: abs(p[0] - c))[: n0 - max_rm]
            if len(keep) < n0 and len(keep) >= max(3, MIN_SAMPLES // 2):
                removed = n0 - len(keep)
                pairs = keep

    mean = wmean(pairs) if len(pairs) >= MIN_SAMPLES else wquantile(pairs, 0.5)
    std = wstd(pairs, mean)
    if len(pairs) >= 3:
        std = max(std, 0.6 * mad_sigma([p[0] for p in pairs]))
    q1 = wquantile(pairs, 0.25)
    q3 = wquantile(pairs, 0.75)
    cv = (std / mean) if mean > 0 else 1.0
    conf = max(0.0, min(1.0, (len(pairs) / (2.0 * MIN_SAMPLES)) * (1 - min(0.5, cv))))

    mv = MarketValue(
        route_key=route_key, cabin_class=cabin, valor_de_mercado=round(mean, 2),
        desvio_padrao=round(std, 2), p25=round(q1, 2), p75=round(q3, 2),
        iqr=round(q3 - q1, 2), min_fare=round(min(p[0] for p in pairs), 2),
        max_fare=round(max(p[0] for p in pairs), 2), n_samples=len(pairs),
        n_outliers=removed, confidence=round(conf, 3),
    )
    durs = [f.duration_minutes for f in fresh if f.duration_minutes > 0]
    ref = sorted(durs)[max(0, int(0.1 * len(durs)) - 1)] if durs else 0.0

    scored = []
    for f in fresh:
        sf = service_factor(f, ref)
        ratio = f.fare_total / mean if mean > 0 else 1.0
        scored.append((round(ratio * sf, 4), f, sf, ratio,
                       f.fare_total < lo, f.fare_total > hi))
    scored.sort(key=lambda t: t[0])
    return mv, scored


# ---------------------------------------------------------------------------
# Banco (sqlite3, stdlib)
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS fares (
  id INTEGER PRIMARY KEY AUTOINCREMENT, fare_hash TEXT, source TEXT,
  origin TEXT, destination TEXT, departure_date TEXT, return_date TEXT,
  cabin_class TEXT, fare_total REAL, currency_original TEXT,
  fare_original REAL, fx_rate REAL, taxes REAL, fees REAL,
  refundable INTEGER, duration_minutes INTEGER, stops INTEGER,
  baggage_included INTEGER, airline TEXT, deep_link TEXT, collected_at TEXT);
CREATE INDEX IF NOT EXISTS idx_fares_route ON fares(origin,destination,departure_date,return_date,cabin_class);
CREATE INDEX IF NOT EXISTS idx_fares_ts ON fares(collected_at);

CREATE TABLE IF NOT EXISTS watches (
  id TEXT PRIMARY KEY, user_id TEXT, origem TEXT, destino TEXT,
  periodo_inicio TEXT, periodo_fim TEXT, porcentagem_alerta REAL,
  cabin_classes TEXT, canais TEXT, frequencia_busca_horas INTEGER,
  debounce_horas INTEGER, queda_adicional_pct REAL, flex_dias INTEGER,
  ativo INTEGER, created_at TEXT, updated_at TEXT, last_scanned_at TEXT);

CREATE TABLE IF NOT EXISTS alerts (
  id TEXT PRIMARY KEY, watch_id TEXT, route TEXT, cabin_class TEXT,
  preco_atual REAL, valor_de_mercado REAL, porcentagem_configurada REAL,
  desconto_real_pct REAL, score REAL, source TEXT, deep_link TEXT,
  departure_date TEXT, return_date TEXT, channels TEXT, reason TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_alerts_watch ON alerts(watch_id, created_at);

CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT, watch_id TEXT, alert_id TEXT,
  channel TEXT, provider TEXT, "to" TEXT, body TEXT, status TEXT, sent_at TEXT);
"""


def connect():
    p = Path(DB_PATH)
    if p.parent and str(p.parent) not in ("", "."):
        p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as c:
        c.executescript(SCHEMA)
    print("[ok] schema criado em %s" % DB_PATH)


def save_fares(fares):
    rows = []
    for f in fares:
        rows.append((f.stable_hash(), f.source, f.origin, f.destination,
                     f.departure_date.isoformat(),
                     f.return_date.isoformat() if f.return_date else None,
                     f.cabin_class, f.fare_total, f.currency_original,
                     f.fare_original, f.fx_rate, f.taxes, f.fees,
                     int(f.refundable), f.duration_minutes, f.stops,
                     int(f.baggage_included), f.airline, f.deep_link,
                     f.timestamp.isoformat()))
    with connect() as c:
        c.executemany(
            "INSERT INTO fares (fare_hash,source,origin,destination,departure_date,"
            "return_date,cabin_class,fare_total,currency_original,fare_original,"
            "fx_rate,taxes,fees,refundable,duration_minutes,stops,baggage_included,"
            "airline,deep_link,collected_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows)
    return len(rows)


def load_history(origin, destination, dep, ret, cabin, hours=168):
    cutoff = (now_utc() - timedelta(hours=hours)).isoformat()
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM fares WHERE origin=? AND destination=? AND cabin_class=?"
            " AND departure_date=? AND (return_date IS ? OR return_date=?)"
            " AND collected_at >= ?",
            (origin, destination, cabin, dep.isoformat(),
             ret.isoformat() if ret else None,
             ret.isoformat() if ret else None, cutoff)).fetchall()
    out = []
    for r in rows:
        ts = datetime.fromisoformat(r["collected_at"])
        out.append(Fare(
            source=r["source"], origin=r["origin"], destination=r["destination"],
            departure_date=date.fromisoformat(r["departure_date"]),
            return_date=date.fromisoformat(r["return_date"]) if r["return_date"] else None,
            cabin_class=r["cabin_class"], fare_total=r["fare_total"],
            currency_original=r["currency_original"], fare_original=r["fare_original"],
            fx_rate=r["fx_rate"], taxes=r["taxes"], fees=r["fees"],
            timestamp=ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc),
            refundable=bool(r["refundable"]), duration_minutes=r["duration_minutes"],
            stops=r["stops"], baggage_included=bool(r["baggage_included"]),
            airline=r["airline"], deep_link=r["deep_link"]))
    return out


def save_alert(a: Alert):
    with connect() as c:
        c.execute(
            "INSERT INTO alerts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (a.id, a.watch_id, a.route, a.cabin_class, a.preco_atual,
             a.valor_de_mercado, a.porcentagem_configurada, a.desconto_real_pct,
             a.score, a.source, a.deep_link, a.departure_date.isoformat(),
             a.return_date.isoformat() if a.return_date else None,
             ",".join(a.channels), a.reason, a.created_at.isoformat()))
        for ch in a.channels:
            c.execute("INSERT INTO notifications (watch_id,alert_id,channel,provider,"
                      '"to",body,status,sent_at) VALUES (?,?,?,?,?,?,?,?)',
                      (a.watch_id, a.id, ch, {"sms": "twilio", "push": "fcm",
                                              "whatsapp": "whatsapp_cloud"}.get(ch, ch),
                       "+5500000000000", build_sms(a), "dry_run", a.created_at.isoformat()))


def last_alert(watch_id, route):
    with connect() as c:
        r = c.execute("SELECT * FROM alerts WHERE watch_id=? AND route=?"
                      " ORDER BY created_at DESC LIMIT 1", (watch_id, route)).fetchone()
    if not r:
        return None
    return {"preco_atual": r["preco_atual"],
            "created_at": datetime.fromisoformat(r["created_at"]).replace(tzinfo=timezone.utc)}


def alerts_today(watch_id):
    start = now_utc().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with connect() as c:
        return c.execute("SELECT COUNT(*) n FROM alerts WHERE watch_id=? AND created_at>=?",
                         (watch_id, start)).fetchone()["n"]


def create_watch(origem, destino, ini, fim, pct, canais, cabins, freq, deb, drop, flex, oneway=0):
    w = {"id": str(uuid.uuid4()), "user_id": "local", "oneway": int(oneway), "origem": origem.upper(),
         "destino": destino.upper(), "periodo_inicio": ini, "periodo_fim": fim,
         "porcentagem_alerta": pct, "cabin_classes": ",".join(cabins),
         "canais": ",".join(canais), "frequencia_busca_horas": freq,
         "debounce_horas": deb, "queda_adicional_pct": drop, "flex_dias": flex}
    ts = now_utc().isoformat()
    with connect() as c:
        c.execute("INSERT INTO watches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (w["id"], w["user_id"], w["origem"], w["destino"], ini, fim or "",
                   pct, w["cabin_classes"], w["canais"], freq, deb, drop, flex, 1, ts, ts, None))
    return w


def list_watches():
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM watches ORDER BY created_at")]


def due_watches():
    out = []
    for w in list_watches():
        if not w["ativo"]:
            continue
        ls = w["last_scanned_at"]
        if not ls:
            out.append(w)
            continue
        last = datetime.fromisoformat(ls).replace(tzinfo=timezone.utc)
        if now_utc() - last >= timedelta(hours=w["frequencia_busca_horas"]):
            out.append(w)
    return out


def touch_scan(watch_id):
    with connect() as c:
        c.execute("UPDATE watches SET last_scanned_at=? WHERE id=?",
                  (now_utc().isoformat(), watch_id))


def get_watch(watch_id):
    with connect() as c:
        r = c.execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()
    return dict(r) if r else None


# ---------------------------------------------------------------------------
# Notificacoes (templates)
# ---------------------------------------------------------------------------
def build_sms(a: Alert) -> str:
    if len(a.route) >= 7:
        route = a.route.replace("-", ">")
    else:
        route = a.route.replace("-", ">")
    d = "%s-%s" % (a.departure_date.strftime("%d/%m"),
                   a.return_date.strftime("%d/%m") if a.return_date else "")
    body = "ALERTA %s %s por %s (%d%% abaixo do mercado) https://voos.ex/r/%s" % (
        route, d, brl(a.preco_atual), round(a.desconto_real_pct), a.id[:8])
    if len(body) > 160:
        body = "%s %s %s (-%d%%) https://voos.ex/r/%s" % (
            route, d, brl(a.preco_atual), round(a.desconto_real_pct), a.id[:8])
    return body[:160]


def build_push(a: Alert):
    route = a.route.replace("-", ">")
    title = ("Passagem %s em promocao" % route)[:40]
    body = ("%s - %d%% abaixo do valor de mercado (%s). Toque para ver."
            % (brl(a.preco_atual), round(a.desconto_real_pct), brl(a.valor_de_mercado)))
    data = {"type": "fare_alert", "alert_id": a.id, "route": a.route,
            "price": "%.2f" % a.preco_atual, "market": "%.2f" % a.valor_de_mercado,
            "url": a.deep_link or "", "action_label": "Ver oferta"}
    return title, body, data


def build_whatsapp(a: Alert) -> str:
    route = a.route.replace("-", ">")
    return ("Oferta %s\n%s a %s - %s\n*%s* (mercado: %s)\n"
            "%.1f%% abaixo do valor de mercado (seu limite: %.0f%%)\n"
            "Score custo-beneficio: %.2f\nFonte: %s\n[Ver oferta] %s"
            % (route, a.departure_date.strftime("%d/%m/%Y"),
               a.return_date.strftime("%d/%m/%Y") if a.return_date else "-",
               a.cabin_class, brl(a.preco_atual), brl(a.valor_de_mercado),
               a.desconto_real_pct, a.porcentagem_configurada,
               a.score, a.source, a.deep_link or ""))


# ---------------------------------------------------------------------------
# Debounce
# ---------------------------------------------------------------------------
def evaluate_debounce(w, price, last, today_count):
    if last is None:
        return True, "first_alert"
    hours = (now_utc() - last["created_at"]).total_seconds() / 3600
    if hours < w["debounce_horas"]:
        drop = (last["preco_atual"] - price) / last["preco_atual"] * 100
        if drop < w["queda_adicional_pct"]:
            return False, "debounce_window_active"
        if today_count >= MAX_ALERTS_DAY:
            return False, "daily_cap_reached"
        return True, "significant_extra_drop"
    if today_count >= MAX_ALERTS_DAY:
        return False, "daily_cap_reached"
    return True, "window_elapsed"


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
def scan_watch(w):
    ini = parse_ddmmyyyy(w["periodo_inicio"])
    oneway = bool(int(w.get("oneway", 0) or 0))
    fim = None if oneway else parse_ddmmyyyy(w["periodo_fim"])
    cabins = [c for c in w["cabin_classes"].split(",") if c]
    canais = [c for c in w["canais"].split(",") if c]

    raw = []
    for src in build_sources():
        for cabin in cabins:
            raw.extend(src.search(w["origem"], w["destino"], ini, fim, cabin, oneway))
    fares = normalize_many(raw)
    written = save_fares(fares)

    groups = {}
    for f in fares:
        groups.setdefault((f.route_key, f.cabin_class), []).append(f)

    markets, alerts, suppressed = [], [], []
    for (rkey, cabin), group in groups.items():
        hist = load_history(w["origem"], w["destino"], ini, fim, cabin)
        mv, scored = compute_market_value(group + hist, cabin, rkey)
        if mv is None:
            continue
        markets.append(mv)

        threshold = (w["porcentagem_alerta"] / 100.0) * mv.valor_de_mercado
        implausible = mv.p25 - 3 * mv.iqr
        cands = [s for s in scored
                 if s[1].fare_total <= threshold
                 and not s[5]                       # nao e outlier alto
                 and mv.iqr > 0 and s[1].fare_total >= implausible]
        if not cands:
            continue
        best = cands[0]
        fare, sf, ratio = best[1], best[2], best[3]

        send, reason = evaluate_debounce(
            w, fare.fare_total, last_alert(w["id"], fare.route), alerts_today(w["id"]))
        if not send:
            suppressed.append({"route_key": rkey, "reason": reason,
                               "price": fare.fare_total})
            continue

        a = Alert(id=str(uuid.uuid4()), watch_id=w["id"], route=fare.route,
                  cabin_class=cabin, preco_atual=fare.fare_total,
                  valor_de_mercado=mv.valor_de_mercado,
                  porcentagem_configurada=w["porcentagem_alerta"],
                  desconto_real_pct=round((1 - ratio) * 100, 2),
                  score=best[0], source=fare.source, departure_date=fare.departure_date,
                  return_date=fare.return_date, deep_link=fare.deep_link,
                  channels=canais, reason=reason)
        save_alert(a)
        alerts.append(a)

    touch_scan(w["id"])
    return {"fares": len(fares), "written": written, "markets": markets,
            "alerts": alerts, "suppressed": suppressed}


def print_report(w, rep):
    print("[%s-%s] coletadas=%d gravadas=%d mercados=%d alertas=%d suprimidos=%d"
          % (w["origem"], w["destino"], rep["fares"], rep["written"],
             len(rep["markets"]), len(rep["alerts"]), len(rep["suppressed"])))
    for m in rep["markets"]:
        print("   - %-8s mercado=%s dp=%s n=%d outliers=%d conf=%.2f"
              % (m.cabin_class, brl(m.valor_de_mercado), brl(m.desvio_padrao),
                 m.n_samples, m.n_outliers, m.confidence))
    for a in rep["alerts"]:
        print("   >> ALERTA %s %s %s (-%.2f%% vs %s) score=%.4f canais=%s motivo=%s"
              % (a.route, a.cabin_class, brl(a.preco_atual), a.desconto_real_pct,
                 brl(a.valor_de_mercado), a.score, a.channels, a.reason))
        print("      SMS  : %s (%d chars)" % (build_sms(a), len(build_sms(a))))
        t, b, _ = build_push(a)
        print("      PUSH : %s | %s" % (t, b))
    for s in rep["suppressed"]:
        print("   [pausa] %s (%s) %s" % (s["route_key"], s["reason"], brl(s["price"])))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def cmd_init_db(a):
    init_db()
    return 0


def cmd_seed(a):
    init_db()
    w = create_watch(
        os.environ.get("FDM_ORIGEM", "GRU"), os.environ.get("FDM_DESTINO", "LAX"),
        os.environ.get("FDM_INICIO", "01-10-2026"),
        os.environ.get("FDM_FIM", "31-12-2026"),
        float(os.environ.get("FDM_PCT", "50")), ["sms", "push"],
        ["economy", "business"], 6, 24, 5.0, 0,
        int(os.environ.get("FDM_ONEWAY", "0")))
    print("[ok] watch criado: %s  %s->%s  %s a %s  <= %.0f%% do mercado"
          % (w["id"], w["origem"], w["destino"], w["periodo_inicio"],
             w["periodo_fim"], w["porcentagem_alerta"]))
    return 0


def cmd_scan(a):
    init_db()
    if a.watch_id:
        w = get_watch(a.watch_id)
        if not w:
            print("[erro] watch %s nao encontrado" % a.watch_id)
            return 1
        targets = [w]
    else:
        targets = due_watches()
    if not targets:
        print("nenhum watch vencido (rode: python flight_monitor.py seed)")
        return 0
    for w in targets:
        print_report(w, scan_watch(w))
    return 0


def cmd_demo(a):
    print("=" * 72)
    print("DEMO - Flight Deal Monitor (standalone, sem dependencias externas)")
    print("=" * 72)
    init_db()
    w = create_watch(
        os.environ.get("FDM_ORIGEM", "GRU"), os.environ.get("FDM_DESTINO", "LAX"),
        os.environ.get("FDM_INICIO", "01-10-2026"),
        os.environ.get("FDM_FIM", "31-12-2026"),
        float(os.environ.get("FDM_PCT", "50")), ["sms", "push", "whatsapp"],
        ["economy", "business"], 6, 24, 5.0, 0,
        int(os.environ.get("FDM_ONEWAY", "0")))
    print("\n1) Configuracao do usuario")
    print("   rota   : %s -> %s" % (w["origem"], w["destino"]))
    print("   periodo: %s%s" % (w["periodo_inicio"],
          "" if w.get("oneway") else " a %s" % w["periodo_fim"]))
    print("   alerta : preco <= %.0f%% do valor de mercado" % w["porcentagem_alerta"])
    print("   canais : %s" % w["canais"])
    print("   watch  : %s\n" % w["id"])

    print("2) Executando scan (coleta -> normalizacao -> mercado -> alerta)\n")
    rep = scan_watch(w)
    print_report(w, rep)

    if rep["markets"]:
        m = rep["markets"][0]
        print("\n3) Como o valor de mercado foi calculado (%s)" % m.cabin_class)
        print("   amostras na janela de 168h : %d" % m.n_samples)
        print("   outliers podados pelo IQR  : %d" % m.n_outliers)
        print("   p25 / p75                  : %s / %s" % (brl(m.p25), brl(m.p75)))
        print("   media ponderada            : %s  <-- valor de mercado" % brl(m.valor_de_mercado))
        print("   desvio padrao              : %s" % brl(m.desvio_padrao))
        print("   menor / maior da amostra   : %s / %s"
              % (brl(m.min_fare), brl(m.max_fare)))

    print("\n4) Segundo scan imediato (o debounce deve silenciar):")
    rep2 = scan_watch(w)
    print_report(w, rep2)
    print("\n   -> alertas=%d suprimidos=%d %s"
          % (len(rep2["alerts"]), len(rep2["suppressed"]),
             [s["reason"] for s in rep2["suppressed"]]))

    if rep["alerts"]:
        a = rep["alerts"][0]
        print("\n5) Mensagens que seriam enviadas (NOTIFY_DRY_RUN)")
        print("   --- SMS (%d chars) ---\n   %s" % (len(build_sms(a)), build_sms(a)))
        t, b, d = build_push(a)
        print("   --- PUSH ---\n   titulo: %s\n   corpo : %s\n   dados : %s" % (t, b, d))
        print("   --- WHATSAPP ---")
        for line in build_whatsapp(a).splitlines():
            print("   " + line)

    print("\n" + "=" * 72)
    print("PRONTO. Banco: %s" % DB_PATH)
    print("  python flight_monitor.py scan     -> novo ciclo")
    print("  python flight_monitor.py seed     -> outro monitoramento")
    print("=" * 72)
    return 0


DEEPLINKS = {
    "decolar": "https://www.decolar.com/shop/flights/results/{tipo}/{o}/{d}/{dep}{ret}/{ad}/0/0",
}

def iso(d):
    return d.strftime("%Y-%m-%d")


def cmd_links(a):
    """Mostra (ou abre) os links de compra para a rota configurada."""
    import webbrowser

    o = a.origem.upper()
    d = a.destino.upper()
    dep = parse_ddmmyyyy(a.ini)
    ret = None if a.oneway else parse_ddmmyyyy(a.fim)
    tipo = "oneway" if ret is None else "roundtrip"

    links = {
        "Decolar": ("https://www.decolar.com/shop/flights/results/%s/%s/%s/%s%s/1/0/0"
                    % (tipo, o, d, iso(dep), "/" + iso(ret) if ret else "")),
        "Google Flights": (
            "https://www.google.com/travel/flights?q="
            + quote("Flights from %s to %s on %s%s" % (o, d, iso(dep),
                    " through " + iso(ret) if ret else ""))),
        "Skyscanner": ("https://www.skyscanner.com.br/transporte/passagens-aereas/%s/%s/%s%s/"
                       % (o.lower(), d.lower(), dep.strftime("%y%m%d"),
                          "/" + ret.strftime("%y%m%d") if ret else "")),
        "Kayak": ("https://www.kayak.com.br/flights/%s-%s/%s%s"
                  % (o, d, iso(dep), "/" + iso(ret) if ret else "")),
        "MaxMilhas": ("https://www.maxmilhas.com.br/busca-passagens-aereas/%s-%s?dataIda=%s%s"
                      % (o, d, iso(dep), "&dataVolta=" + iso(ret) if ret else "")),
    }

    print("=" * 72)
    print("LINKS DE COMPRA  %s -> %s   %s%s" % (o, d, iso(dep),
          "" if ret is None else " a " + iso(ret)))
    print("=" * 72)
    for nome, url in links.items():
        print("  %-15s %s" % (nome, url))
    print()
    if a.abrir:
        try:
            webbrowser.open(links[a.abrir])
            print("[ok] abrindo %s no navegador..." % a.abrir)
        except Exception as exc:
            print("     nao foi possivel abrir (%s)" % exc)
    return 0


def cmd_painel(a):
    """Abre o painel visual (painel.html) no navegador padrao."""
    import webbrowser

    alvo = None
    for cand in (Path(__file__).resolve().parent / "painel.html",
                 Path.cwd() / "painel.html"):
        if cand.exists():
            alvo = cand
            break
    if alvo is None:
        print("[erro] painel.html nao encontrado.")
        print("       Deixe painel.html na mesma pasta deste script.")
        return 1
    url = alvo.resolve().as_uri()
    print("[ok] abrindo o painel: %s" % alvo)
    print("     (se o navegador nao abrir, abra esse arquivo com duplo clique)")
    try:
        webbrowser.open(url)
    except Exception as exc:
        print("     nao foi possivel abrir automaticamente (%s)" % exc)
    return 0


def cmd_watches(a):
    init_db()
    ws = list_watches()
    if not ws:
        print("nenhum watch. Rode: python flight_monitor.py seed")
        return 0
    for w in ws:
        print("%s  %s->%s  %s a %s  <=%.0f%%  canais=%s  ultimo=%s"
              % (w["id"][:8], w["origem"], w["destino"], w["periodo_inicio"],
                 w["periodo_fim"], w["porcentagem_alerta"], w["canais"],
                 (w["last_scanned_at"] or "-")[:19]))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="flight_monitor",
                                description="Monitor de passagens (standalone, stdlib only)")
    p.add_argument("--db", default=None, help="caminho do SQLite")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("init-db").set_defaults(f=cmd_init_db)
    sub.add_parser("seed").set_defaults(f=cmd_seed)
    sub.add_parser("demo").set_defaults(f=cmd_demo)
    sub.add_parser("painel").set_defaults(f=cmd_painel)
    sub.add_parser("watches").set_defaults(f=cmd_watches)
    sp = sub.add_parser("links")
    sp.add_argument("--origem", default=os.environ.get("FDM_ORIGEM", "GRU"))
    sp.add_argument("--destino", default=os.environ.get("FDM_DESTINO", "LAX"))
    sp.add_argument("--ini", default=os.environ.get("FDM_INICIO", "05-10-2026"))
    sp.add_argument("--fim", default=os.environ.get("FDM_FIM", "19-10-2026"))
    sp.add_argument("--oneway", action="store_true")
    sp.add_argument("--abrir", default=None,
                    help="abre direto: decolar | google | skyscanner | kayak | maxmilhas")
    sp.set_defaults(f=cmd_links)

    sp = sub.add_parser("scan")
    sp.add_argument("--watch-id", default=None)
    sp.set_defaults(f=cmd_scan)

    args = p.parse_args(argv)
    if args.db:
        global DB_PATH
        DB_PATH = args.db
    if not getattr(args, "f", None):
        p.print_help()
        return 0
    return args.f(args)


if __name__ == "__main__":
    sys.exit(main())
