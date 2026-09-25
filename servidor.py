#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Servidor do painel com **preços reais** — um comando só, sem chave, sem custo.

Uso (no VSCode, Terminal -> Novo Terminal, dentro da pasta do projeto):

    python servidor.py

Depois abra no navegador:

    http://localhost:8000/painel.html

O que ele faz de diferente de `python -m http.server 8000`:
  * serve os arquivos do painel (igual ao http.server);
  * expoe `GET /api/busca`, que vai ao Google Flights e traz precos de verdade.

Usa apenas a biblioteca padrao do Python + `fast-flights` (pip install fast-flights).
Nao precisa de chave de API, nao precisa de cartao, nao custa nada.

LIMITACAO CONHECIDA (medida em 29/08/2026, GRU->REC 01/10, so ida):
    O conector devolve R$ 697 como menor tarifa; a pagina do Google Flights
    anuncia "a partir de R$ 599". Diferenca de ~13%.
    Motivo: o "a partir de" do Google inclui tarifas basicas e ofertas de OTAs
    que nao aparecem na lista principal de voos que a biblioteca le.
    Ou seja: o numero do Google e o piso absoluto; o nosso e o menor entre os
    voos efetivamente listados — portanto mais conservador.
    Nao e erro de parse: a lista de voos (cia, horarios, duracao, aeronave)
    confere com a pagina.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATE_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")

CABINAS = {
    "economy": "Economica",
    "premium-economy": "Premium",
    "business": "Executiva",
    "first": "Primeira",
}


# --------------------------------------------------------------------------
# datas
# --------------------------------------------------------------------------
def parse_ddmmyyyy(txt: str) -> date:
    m = DATE_RE.match((txt or "").strip())
    if not m:
        raise ValueError(f"Data invalida: {txt!r} (use DD-MM-AAAA)")
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return date(y, mo, d)


# --------------------------------------------------------------------------
# estatistica pura (sem numpy) — mesma convencao do src/market.py
# --------------------------------------------------------------------------
def _quantile(vs: list[float], q: float) -> float:
    if not vs:
        return 0.0
    if len(vs) == 1:
        return vs[0]
    pos = q * (len(vs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(vs) - 1)
    return vs[lo] + (vs[hi] - vs[lo]) * (pos - lo)


def estatisticas(valores: list[float]) -> dict:
    """Media aparada por IQR (poda unica, limite de 25% de remocao)."""
    vs = sorted(float(v) for v in valores if v and v > 0)
    if not vs:
        return {}
    q1, q3 = _quantile(vs, 0.25), _quantile(vs, 0.75)
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    mantidos = [x for x in vs if lo <= x <= hi] or vs
    # nunca descarta mais de 25% da amostra
    minimo = max(1, int(len(vs) * 0.75))
    if len(mantidos) < minimo:
        mantidos = vs
    media = sum(mantidos) / len(mantidos)
    return {
        "n": len(vs),
        "n_outliers": len(vs) - len(mantidos),
        "media": round(media, 2),
        "mediana": round(_quantile(vs, 0.5), 2),
        "p25": round(q1, 2),
        "p75": round(q3, 2),
        "iqr": round(iqr, 2),
        "min": round(vs[0], 2),
        "max": round(vs[-1], 2),
    }


# --------------------------------------------------------------------------
# busca real no Google Flights
# --------------------------------------------------------------------------
_cache: dict[tuple, tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()
CACHE_TTL = 15 * 60.0


def _chamar(origem: str, destino: str, dep: date, ret: date | None,
            cabina: str, forcar: bool = False) -> list[dict]:
    """Uma consulta ao Google Flights. Devolve lista de ofertas (pode ser vazia).

    `forcar=True` ignora o cache — usado pelo monitoramento, que existe
    justamente para pegar variacao de preco ao longo do tempo.
    """
    chave = (origem, destino, dep, ret, cabina)
    import time
    if not forcar:
        with _cache_lock:
            hit = _cache.get(chave)
            if hit and time.monotonic() - hit[0] < CACHE_TTL:
                return hit[1]

    try:
        from fast_flights import FlightQuery, Passengers, create_filter, get_flights
    except ImportError:
        raise RuntimeError("fast_flights_ausente")

    pernas = [FlightQuery(date=dep.isoformat(), from_airport=origem, to_airport=destino)]
    if ret:
        pernas.append(FlightQuery(date=ret.isoformat(), from_airport=destino, to_airport=origem))

    q = create_filter(
        flights=pernas,
        trip="round-trip" if ret else "one-way",
        seat=cabina,
        passengers=Passengers(adults=1),
        language="pt-BR",
        currency="BRL",
    )

    try:
        resultado = get_flights(q)
    except (TypeError, IndexError, KeyError, ValueError, AttributeError):
        # Google nao devolveu nada: acontece em rota domestica pedida em
        # executiva (nao existe cabine executiva ali). Nao e erro.
        with _cache_lock:
            _cache[chave] = (time.monotonic(), [])
        return []
    except Exception as exc:
        raise RuntimeError(f"{type(exc).__name__}: {exc}")

    ofertas: list[dict] = []
    for item in resultado:
        try:
            preco = float(item.price)
            if preco <= 0:
                continue
            trechos = list(getattr(item, "flights", []) or [])
            minutos = 0
            for t in trechos:
                try:
                    d0, h0 = t.departure.date, t.departure.time
                    d1, h1 = t.arrival.date, t.arrival.time
                    minutos += int((datetime(*d1, *h1[:2]) - datetime(*d0, *h0[:2])).total_seconds() // 60)
                except Exception:
                    pass
            saida = chegada = None
            if trechos:
                try:
                    saida = "%02d:%02d" % (trechos[0].departure.time[0], trechos[0].departure.time[1])
                    chegada = "%02d:%02d" % (trechos[-1].arrival.time[0], trechos[-1].arrival.time[1])
                except Exception:
                    pass
            ofertas.append({
                "preco": round(preco, 2),
                "cia": (item.airlines[0] if getattr(item, "airlines", None) else "?"),
                "cia_codigo": getattr(item, "type", "") or "",
                "paradas": max(0, len(trechos) - 1),
                "duracao_min": minutos,
                "saida": saida,
                "chegada": chegada,
                "aeronave": getattr(trechos[0], "plane_type", None) if trechos else None,
                "data_ida": dep.isoformat(),
                "data_volta": ret.isoformat() if ret else None,
                "link": link_google(origem, destino, dep, ret),
            })
        except Exception:
            continue

    with _cache_lock:
        _cache[chave] = (time.monotonic(), ofertas)
    return ofertas


def link_google(o: str, d: str, dep: date, ret: date | None) -> str:
    from urllib.parse import quote
    txt = f"Flights from {o} to {d} on {dep.isoformat()}"
    if ret:
        txt += f" through {ret.isoformat()}"
    return "https://www.google.com/travel/flights?q=" + quote(txt)


def links_compra(o: str, d: str, dep: date, ret: date | None) -> dict:
    tipo = "roundtrip" if ret else "oneway"
    i1, i2 = dep.isoformat(), (ret.isoformat() if ret else None)
    yy = lambda x: x.strftime("%y%m%d")
    return {
        "Decolar": f"https://www.decolar.com/shop/flights/results/{tipo}/{o}/{d}/{i1}"
                   + (f"/{i2}" if i2 else "") + "/1/0/0",
        "Google Flights": link_google(o, d, dep, ret),
        "Skyscanner": f"https://www.skyscanner.com.br/transporte/passagens-aereas/"
                      f"{o.lower()}/{d.lower()}/{yy(dep)}" + (f"/{yy(ret)}" if ret else "") + "/",
        "Kayak": f"https://www.kayak.com.br/flights/{o}-{d}/{i1}" + (f"/{i2}" if i2 else ""),
        "Momondo": (f"https://www.momondo.com.br/flight-search/{o}-{d}/{i1}"
                    + (f"/{i2}" if i2 else "/") + "?adults=1&cabinclass=economy"),
        "MaxMilhas": f"https://www.maxmilhas.com.br/busca-passagens-aereas/{o}-{d}"
                     f"?dataIda={i1}" + (f"&dataVolta={i2}" if i2 else ""),
    }


def buscar_cabina(origem: str, destino: str, dep: date, ret: date | None,
                  cabina: str, pct: float, pulo: int = 2, forcar: bool = False) -> dict:
    """Consulta algumas datas em volta para montar amostra real de mercado."""
    dias = [dep + timedelta(days=k) for k in range(-pulo, pulo + 1)]
    pares = [(d, None if ret is None else ret + timedelta(days=k))
             for d, k in zip(dias, range(-pulo, pulo + 1))]

    todas: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        fut = [pool.submit(_chamar, origem, destino, a, b, cabina, forcar) for a, b in pares]
        for f in fut:
            try:
                todas.extend(f.result())
            except Exception as exc:
                if "fast_flights_ausente" in str(exc):
                    raise
    if not todas:
        return {"cabina": cabina, "nome": CABINAS.get(cabina, cabina),
                "indisponivel": True, "ofertas": [], "amostras": 0}

    precos = [o["preco"] for o in todas]
    st = estatisticas(precos)
    mercado = st["media"]
    limite = pct / 100.0 * mercado

    # deduplicacao por (cia, paradas, duracao, preco)
    vistos, unicas = set(), []
    for o in sorted(todas, key=lambda x: x["preco"]):
        ch = (o["cia"], o["paradas"], o["duracao_min"], o["preco"], o["saida"])
        if ch in vistos:
            continue
        vistos.add(ch)
        unicas.append(o)

    melhor = unicas[0]
    delta = (melhor["preco"] - mercado) / mercado * 100.0 if mercado else 0.0
    return {
        "cabina": cabina,
        "nome": CABINAS.get(cabina, cabina),
        "indisponivel": False,
        "mercado": st,
        "limite_alerta": round(limite, 2),
        "melhor": melhor,
        "delta_pct": round(delta, 1),
        "dispara_alerta": melhor["preco"] <= limite,
        "ofertas": unicas[:60],
    }



# ==========================================================================
# Monitoramento continuo (prompt 4): roda sozinho a cada N MINUTOS.
#
# Regra de alerta (igual a do motor):
#     dispara quando  preco <= (pct / 100) * valor_de_mercado
#
# Debounce (ordem adotada no projeto):
#     1. primeiro alerta da rota                 -> first_alert
#     2. dentro da janela e sem queda relevante  -> silenciado
#     3. queda adicional >= drop_pct             -> significant_extra_drop
#     4. janela vencida (>= debounce_horas)      -> window_elapsed
#
# O estado fica em monitoramento.json, entao sobrevive a fechar o navegador
# e a reiniciar o servidor (se ele for reiniciado, retoma de onde parou).
# ==========================================================================
MON_FILE = ROOT / "monitoramento.json"
MON_LOCK = threading.Lock()
MON_MIN_MINUTOS = 10          # piso de seguranca (evita bloqueio do Google)
MON_MAX_CICLOS = 200          # tamanho do historico de ciclos
MON_MAX_ALERTAS = 100

MON = {
    "ativo": False,
    "intervalo_min": 30,
    "rotas": [],
    "alertas": [],
    "ciclos": [],
    "ultimo_alerta": {},   # "ROTA|cabina" -> {"preco":..,"em":..}
    "erros_consecutivos": 0,
    "iniciado_em": None,
    "ultimo_ciclo": None,
    "ntfy_topico": "",
    "ntfy_ativado": False,
}


def mon_salvar() -> None:
    with MON_LOCK:
        dados = dict(MON)
    try:
        MON_FILE.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def mon_carregar() -> None:
    if not MON_FILE.exists():
        return
    try:
        dados = json.loads(MON_FILE.read_text(encoding="utf-8"))
        for k in MON:
            if k in dados:
                MON[k] = dados[k]
    except Exception:
        pass


def _problema_topico(t: str) -> str:
    """Valida o nome do canal ntfy. Devolve '' se ok, senao a mensagem.

    Motivo: o campo ja foi confundido com "numero de telefone". Alem da
    confusao, um topico ntfy.sh e PUBLICO — qualquer pessoa que adivinhar
    o nome pode se inscrever e ler os alertas. Entao exigimos um nome
    minimamente longo e nao puramente numerico.
    """
    import re as _re
    t = (t or "").strip()
    if not t:
        return ""
    if len(t) < 8:
        return ("Nome de canal curto demais (minimo 8 caracteres). "
                "Toque em 'Gerar nome' para criar um bem seguro.")
    if len(t) > 64:
        return "Nome de canal muito longo (maximo 64 caracteres)."
    if t.isdigit():
        return ("Isso parece um numero de telefone, mas este campo NAO e SMS.\n\n"
                "Aqui vai o nome de um CANAL do app ntfy — um nome que voce "
                "mesmo inventa (ou toca em 'Gerar nome').\n\n"
                "Como instalar:\n"
                "1. Instale o app 'ntfy' no celular (gratis, sem cadastro)\n"
                "2. No app: botao + > Subscribe to topic\n"
                "3. Digite o mesmo nome do canal")
    if not _re.fullmatch(r"[A-Za-z0-9_-]+", t):
        return ("Use apenas letras sem acento, numeros, hifen (-) e underline (_). "
                "Sem espacos, acentos ou pontuacao.")
    return ""


def mon_notificar(titulo: str, corpo: str) -> str:
    """Push gratis no celular via ntfy.sh (sem conta, sem cartao).

    Para receber: instale o app ntfy (Android/iOS), inscreva-se no topico
    escolhido e ponha o mesmo nome em `ntfy_topico` no painel.
    """
    topico = (MON.get("ntfy_topico") or "").strip()
    if not MON.get("ntfy_ativado") or not topico:
        return ""
    import urllib.request
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{topico}",
            data=corpo.encode("utf-8"),
            method="POST",
            headers={"Title": titulo, "Tags": "airplane,chart_with_downwards_trend"},
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            return f"push enviado (HTTP {r.status})"
    except Exception as exc:
        return f"push falhou: {type(exc).__name__}"


def mon_decidir(rota: dict, cabina: dict, agora: datetime) -> tuple[bool, str]:
    """Aplica a regra de alerta + debounce. Devolve (dispara, motivo)."""
    chave = f"{rota['origem']}-{rota['destino']}|{cabina['cabina']}"
    preco = cabina["melhor"]["preco"]
    limite = cabina["limite_alerta"]

    if preco > limite:
        return False, "acima_limite"

    ant = MON["ultimo_alerta"].get(chave)
    if not ant:
        return True, "first_alert"

    horas = (agora - datetime.fromisoformat(ant["em"])).total_seconds() / 3600.0
    debounce_h = float(rota.get("deb", 24))
    queda = (ant["preco"] - preco) / ant["preco"] * 100.0 if ant["preco"] else 0.0
    extra = float(rota.get("drop", 5))

    if horas >= debounce_h:
        return True, "window_elapsed"
    if queda >= extra:
        return True, "significant_extra_drop"
    return False, "debounce_window_active"


class Monitor(threading.Thread):
    """Loop de verificacao. Uma thread, daemon, para nao prender o processo."""

    daemon = True

    def __init__(self) -> None:
        super().__init__(name="monitor")
        self._parar = threading.Event()

    def parar(self) -> None:
        self._parar.set()

    def run(self) -> None:
        # primeiro ciclo imediato: quem clica em "iniciar" quer ver resultado agora
        try:
            self.ciclo()
        except Exception as exc:
            sys.stderr.write(f"[monitor] erro no primeiro ciclo: {type(exc).__name__}: {exc}\n")
        while not self._parar.is_set():
            try:
                self.ciclo()
            except Exception as exc:
                with MON_LOCK:
                    MON["erros_consecutivos"] += 1
                sys.stderr.write(f"[monitor] erro no ciclo: {type(exc).__name__}: {exc}\n")
            # intervalo em minutos -> segundos
            with MON_LOCK:
                minutos = max(MON_MIN_MINUTOS, int(MON["intervalo_min"] or 30))
            self._parar.wait(minutos * 60)

    def ciclo(self) -> None:
        agora = datetime.now()
        with MON_LOCK:
            rotas = list(MON["rotas"])
        if not rotas:
            return

        for rota in rotas:
            dep = parse_ddmmyyyy(rota["ini"])
            ret = None if rota.get("tipo") == "ow" else parse_ddmmyyyy(rota["fim"])
            pct = float(rota.get("pct", 50))
            for cab in ("economy", "business"):
                try:
                    c = buscar_cabina(rota["origem"], rota["destino"], dep, ret,
                                      cab, pct, pulo=0, forcar=True)
                except Exception as exc:
                    sys.stderr.write(f"[monitor] {rota['origem']}-{rota['destino']} {cab}: {exc}\n")
                    continue
                if c.get("indisponivel"):
                    continue

                dispara, motivo = mon_decidir(rota, c, agora)
                with MON_LOCK:
                    MON["ciclos"].insert(0, {
                        "em": agora.strftime("%d-%m %H:%M"),
                        "rota": f"{rota['origem']}-{rota['destino']}",
                        "cabina": c["nome"],
                        "preco": c["melhor"]["preco"],
                        "mercado": c["mercado"]["media"],
                        "limite": c["limite_alerta"],
                        "cia": c["melhor"]["cia"],
                        "dispara": dispara,
                        "motivo": motivo,
                    })
                    MON["ciclos"] = MON["ciclos"][:MON_MAX_CICLOS]

                if not dispara:
                    continue

                chave = f"{rota['origem']}-{rota['destino']}|{cab}"
                alerta = {
                    "em": agora.strftime("%d-%m-%Y %H:%M"),
                    "rota": f"{rota['origem']}-{rota['destino']}",
                    "ida": rota["ini"], "volta": rota.get("fim") or "",
                    "cabina": c["nome"],
                    "preco": c["melhor"]["preco"],
                    "mercado": c["mercado"]["media"],
                    "desconto": c["delta_pct"],
                    "cia": c["melhor"]["cia"],
                    "paradas": c["melhor"]["paradas"],
                    "motivo": motivo,
                    "link": c["melhor"]["link"],
                }
                push = mon_notificar(
                    f"PASSAGEM {alerta['rota']} {brl_txt(alerta['preco'])}",
                    f"{alerta['cia']} · {alerta['cabina']} · {alerta['desconto']}% abaixo do mercado\n"
                    f"Mercado {brl_txt(alerta['mercado'])} · ida {alerta['ida']}\n{alerta['link']}",
                )
                if push:
                    alerta["push"] = push
                with MON_LOCK:
                    MON["alertas"].insert(0, alerta)
                    MON["alertas"] = MON["alertas"][:MON_MAX_ALERTAS]
                    MON["ultimo_alerta"][chave] = {"preco": alerta["preco"],
                                                   "em": agora.isoformat()}
        with MON_LOCK:
            MON["erros_consecutivos"] = 0
            MON["ultimo_ciclo"] = agora.strftime("%d-%m-%Y %H:%M")
        mon_salvar()


def brl_txt(v: float) -> str:
    return "R$ {:,.2f}".format(v).replace(",", "X").replace(".", ",").replace("X", ".")


_MON: Monitor | None = None


def mon_iniciar(minutos: int, rotas: list[dict], ntfy: str = "") -> None:
    global _MON
    mon_parar()
    with MON_LOCK:
        MON["ativo"] = True
        MON["intervalo_min"] = max(MON_MIN_MINUTOS, int(minutos))
        MON["rotas"] = rotas
        MON["iniciado_em"] = datetime.now().strftime("%d-%m-%Y %H:%M")
        MON["erros_consecutivos"] = 0
        if ntfy:
            MON["ntfy_topico"] = ntfy.strip()
            MON["ntfy_ativado"] = True
    _MON = Monitor()
    _MON.start()
    mon_salvar()


def mon_parar() -> None:
    global _MON
    if _MON is not None:
        _MON.parar()
        _MON = None
    with MON_LOCK:
        MON["ativo"] = False
    mon_salvar()



# ==========================================================================
# Varredura de periodo: o usuario usa "inicio" e "fim" como um INTERVALO de
# possiveis datas de ida, e quer saber qual dia e o mais barato.
# Nao e uma viagem de 3 meses: e um leque de opcoes.
# ==========================================================================
MAX_DIAS_VARREDURA = 120


def varrer_periodo(origem: str, destino: str, ini: date, fim: date,
                   tipo: str, duracao: int, cabina: str = "economy",
                   max_dias: int = MAX_DIAS_VARREDURA,
                   workers: int = 5) -> dict:
    dias, d = [], ini
    while d <= fim and len(dias) < max_dias:
        dias.append(d)
        d += timedelta(days=1)
    if not dias:
        return {"ok": False, "erro": "Intervalo de datas vazio."}

    def _um(dep: date):
        ret = None if tipo == "ow" else dep + timedelta(days=max(0, int(duracao)))
        try:
            ofertas = _chamar(origem, destino, dep, ret, cabina)
        except Exception:
            return None
        if not ofertas:
            return None
        m = min(ofertas, key=lambda x: x["preco"])
        return {
            "data": dep.strftime("%d-%m-%Y"),
            "dia_semana": DIAS_SEMANA[dep.weekday()],
            "iso": dep.isoformat(),
            "volta": ret.isoformat() if ret else None,
            "preco": m["preco"], "cia": m["cia"],
            "paradas": m["paradas"], "duracao_min": m["duracao_min"],
            "saida": m["saida"], "chegada": m["chegada"],
            "link": m["link"],
        }

    achados = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for r in pool.map(_um, dias):
            if r:
                achados.append(r)

    if not achados:
        return {
            "ok": False,
            "erro": ("Nenhuma tarifa encontrada em nenhum dia do intervalo. "
                     "Tente outro periodo ou outra classe."),
        }

    precos = [a["preco"] for a in achados]
    ordenada = sorted(achados, key=lambda a: a["preco"])
    media = sum(precos) / len(precos)
    qs = sorted(precos)
    mediana = qs[len(qs) // 2] if len(qs) % 2 else (qs[len(qs)//2 - 1] + qs[len(qs)//2]) / 2

    return {
        "ok": True,
        "fonte": "Google Flights (consulta direta, sem chave)",
        "consultado_em": datetime.now().strftime("%d-%m-%Y %H:%M"),
        "rota": f"{origem}-{destino}",
        "tipo": tipo,
        "duracao_viagem_dias": None if tipo == "ow" else int(duracao),
        "dias_consultados": len(dias),
        "dias_com_preco": len(achados),
        "dias_sem_preco": len(dias) - len(achados),
        "menor": ordenada[0],
        "mais_caro": ordenada[-1],
        "media": round(media, 2),
        "mediana": round(mediana, 2),
        "economia_vs_media": round((1 - ordenada[0]["preco"] / media) * 100, 1) if media else 0,
        "dias": ordenada,
    }


DIAS_SEMANA = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def probe_conector() -> list[dict]:
    """Importa o conector e cada dependencia, um por um, devolvendo o erro real.

    Motivo: 'pip install' dizer 'Requirement already satisfied' NAO significa
    que o pacote carrega. primp e selectolax sao extensoes compiladas (Rust/C)
    e no Windows falham por falta do Visual C++ Redistributable — a unica forma
    de saber e tentar importar e olhar o traceback.
    """
    import importlib
    import traceback

    saida = []
    for mod in ("primp", "selectolax", "google.protobuf", "fast_flights"):
        try:
            m = importlib.import_module(mod)
            saida.append({"mod": mod, "ok": True,
                          "versao": getattr(m, "__version__", "") or ""})
        except Exception as exc:
            tb = traceback.format_exc().strip().splitlines()
            saida.append({
                "mod": mod, "ok": False,
                "erro": f"{type(exc).__name__}: {exc}",
                "ultima_linha": tb[-1].strip() if tb else "",
            })
    return saida


def _mon_estado() -> dict:
    with MON_LOCK:
        dados = {k: MON.get(k) for k in
                 ("ativo", "intervalo_min", "rotas", "alertas", "ciclos",
                  "ultimo_alerta", "erros_consecutivos", "iniciado_em",
                  "ultimo_ciclo", "ntfy_topico", "ntfy_ativado")}
    dados["ok"] = True
    dados["minimo_minutos"] = MON_MIN_MINUTOS
    if dados["ativo"]:
        inicio = dados.get("iniciado_em")
        dados["proximo_ciclo_em_min"] = dados["intervalo_min"]
    return dados


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write("[servidor] " + fmt % args + "\n")

    def _json(self, obj: dict, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _get_varrer(self, u):
        p = urllib.parse.parse_qs(u.query)
        try:
            origem = (p.get("origem", ["GRU"])[0] or "GRU").upper()
            destino = (p.get("destino", ["REC"])[0] or "REC").upper()
            ini = parse_ddmmyyyy(p.get("ini", [""])[0])
            fim = parse_ddmmyyyy(p.get("fim", [""])[0])
        except ValueError as exc:
            return self._json({"ok": False, "erro": str(exc)}, 400)
        if fim < ini:
            return self._json({"ok": False, "erro": "A data final vem antes da inicial."}, 400)

        tipo = (p.get("tipo", ["ow"])[0] or "ow").lower()
        try:
            duracao = int(p.get("dias", ["7"])[0] or 7)
        except ValueError:
            duracao = 7
        cabina = (p.get("classe", ["economy"])[0] or "economy").lower()

        if (fim - ini).days + 1 > MAX_DIAS_VARREDURA:
            return self._json({
                "ok": False,
                "erro": (f"Intervalo de {(fim - ini).days + 1} dias e longo demais "
                         f"(maximo {MAX_DIAS_VARREDURA})."),
                "como_resolver": "Divida em dois periodos menores.",
            }, 400)

        try:
            import fast_flights  # noqa: F401
        except ImportError:
            return self._json({
                "ok": False, "erro": "Falta instalar o conector gratuito.",
                "como_resolver": "No terminal do VSCode: pip install fast-flights",
            }, 500)

        try:
            return self._json(varrer_periodo(origem, destino, ini, fim, tipo, duracao, cabina))
        except Exception as exc:
            return self._json({"ok": False, "erro": f"{type(exc).__name__}: {exc}"}, 502)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/api/monitor":
            self.send_error(404); return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            body = {}
        acao = (body.get("acao") or "").lower()
        if acao == "parar":
            mon_parar()
            return self._json(_mon_estado())
        if acao == "testar":
            topo = (body.get("ntfy") or "").strip()
            prob = _problema_topico(topo)
            if prob:
                return self._json({"ok": False, "erro": prob}, 400)
            if topo:
                with MON_LOCK:
                    MON["ntfy_topico"] = topo
                    MON["ntfy_ativado"] = True
                mon_salvar()
            ok = mon_notificar("Teste de alerta",
                               "Se voce recebeu isto no celular, o aviso esta funcionando.")
            if ok.startswith("push enviado"):
                msg = ("Enviado! Abra o app ntfy no celular — a mensagem "
                       f"'Teste de alerta' chega em ate 5 segundos.\n\n"
                       f"Canal: {topo or '(nenhum)'}\n\n"
                       "Se nao chegou, confira se voce se inscreveu nesse mesmo "
                       "nome dentro do app (botao + > Subscribe to topic).")
            elif ok:
                msg = "Nao consegui enviar: " + ok
            else:
                msg = ("Nenhum canal preenchido. Toque em 'Gerar nome', "
                       "coloque esse nome no app ntfy e depois em 'Testar aviso'.")
            return self._json({"ok": True, "push": msg})
        if acao != "iniciar":
            return self._json({"ok": False, "erro": "acao deve ser iniciar|parar|testar"}, 400)

        ntfy = (body.get("ntfy") or "").strip()
        prob = _problema_topico(ntfy)
        if prob:
            return self._json({"ok": False, "erro": prob}, 400)
        minutos = int(body.get("minutos") or 30)
        rotas = body.get("rotas") or []
        limpas = []
        for r in rotas:
            try:
                dep = parse_ddmmyyyy(r["ini"])
                ret = None if r.get("tipo") == "ow" else parse_ddmmyyyy(r["fim"])
            except Exception as exc:
                return self._json({"ok": False, "erro": str(exc)}, 400)
            limpas.append({
                "origem": (r["origem"] or "").upper(), "destino": (r["destino"] or "").upper(),
                "ini": r["ini"], "fim": r.get("fim") or "", "tipo": r.get("tipo") or "rt",
                "pct": float(r.get("pct") or 50),
                "deb": float(r.get("deb") or 24), "drop": float(r.get("drop") or 5),
            })
        if not limpas:
            return self._json({"ok": False, "erro": "informe ao menos uma rota."}, 400)
        if minutos < MON_MIN_MINUTOS:
            minutos = MON_MIN_MINUTOS
        mon_iniciar(minutos, limpas, ntfy)
        return self._json(_mon_estado())

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/varrer":
            return self._get_varrer(u)
        if u.path == "/api/ping":
            return self._json({"ok": True, "servidor": "precos-reais", "versao": "2026-08-29c"})
        if u.path == "/api/monitor":
            return self._json(_mon_estado())
        if u.path != "/api/busca":
            return super().do_GET()

        p = urllib.parse.parse_qs(u.query)
        try:
            origem = (p.get("origem", ["GRU"])[0] or "GRU").upper()
            destino = (p.get("destino", ["REC"])[0] or "REC").upper()
            ini = parse_ddmmyyyy(p.get("ini", [""])[0])
            tipo = (p.get("tipo", ["rt"])[0] or "rt").lower()
            pct = float(p.get("pct", ["50"])[0] or 50)
            ret = None if tipo in ("ow", "oneway", "ida") else parse_ddmmyyyy(p.get("fim", [""])[0])
        except ValueError as exc:
            return self._json({"ok": False, "erro": str(exc)}, 400)

        try:
            import fast_flights  # noqa: F401
        except Exception as exc:
            pr = probe_conector()
            ruim = [x for x in pr if not x["ok"]]
            import re as _re
            dica = ""
            txt = f"{type(exc).__name__}: {exc}".lower()
            _m = _re.search(r"no module named '([^']+)'", txt)
            if _m:
                _mod = _m.group(1)
                _pkg = ("protobuf" if _mod.startswith("google")
                        else "fast-flights" if _mod.startswith("fast_flights")
                        else _mod.split(".")[0])
                dica = (f"Falta o pacote '{_pkg}'. O INICIAR.bat instala sozinho; "
                        f"se quiser fazer a mao, no terminal do VSCode: "
                        f"pip install {_pkg}")
            elif "dll load failed" in txt or "could not be found" in txt:
                dica = ("Falta o Visual C++ Redistributable no Windows. "
                        "Baixe e instale o 'vc_redist.x64.exe' em "
                        "https://aka.ms/vs/17/release/vc_redist.x64.exe "
                        "e depois feche e abra o INICIAR.bat de novo.")
            else:
                dica = "Rode DIAGNOSTICO.bat e me envie o diagnostico.txt"
            return self._json({
                "ok": False,
                "erro": f"O conector esta instalado mas nao carrega: {type(exc).__name__}: {exc}",
                "falhou_em": ", ".join(x["mod"] for x in ruim) or "desconhecido",
                "probe": pr,
                "como_resolver": dica or "Rode DIAGNOSTICO.bat e me envie o diagnostico.txt",
            }, 500)

        if ret is not None:
            span = (ret - ini).days
            if span > 60:
                return self._json({
                    "ok": False,
                    "erro": (f"A volta esta {span} dias depois da ida. "
                             f"O Google Flights nao devolve tarifas para um "
                             f"intervalo tao longo (limite pratico: ~60 dias)."),
                    "como_resolver": ("Use uma data de volta mais proxima da ida "
                                      "(ex.: 01-10-2026 a 08-10-2026)."),
                }, 400)
            if span < 0:
                return self._json({"ok": False,
                                   "erro": "A volta nao pode ser antes da ida."}, 400)

        try:
            economica = buscar_cabina(origem, destino, ini, ret, "economy", pct)
            executiva = buscar_cabina(origem, destino, ini, ret, "business", pct)
        except Exception as exc:
            return self._json({"ok": False, "erro": f"{type(exc).__name__}: {exc}"}, 502)

        if economica.get("indisponivel") and executiva.get("indisponivel"):
            return self._json({
                "ok": False,
                "erro": ("O Google Flights nao devolveu nenhuma tarifa para essa "
                         "rota nessas datas."),
                "como_resolver": ("Confira se a sigla do aeroporto esta certa "
                                  "(IGU = Foz do Iguacu, IGR = Puerto Iguazu) e "
                                  "tente uma data de volta mais proxima."),
            }, 404)

        return self._json({
            "ok": True,
            "fonte": "Google Flights (consulta direta, sem chave)",
            "consultado_em": datetime.now().strftime("%d-%m-%Y %H:%M"),
            "rota": f"{origem}-{destino}",
            "ida": ini.isoformat(),
            "volta": ret.isoformat() if ret else None,
            "pct": pct,
            "links": links_compra(origem, destino, ini, ret),
            "classes": [economica, executiva],
        })


def _porta_livre(inicial: int, tentativas: int = 25) -> int:
    """Primeira porta livre a partir de `inicial`.

    Motivo real: se um `python -m http.server 8000` antigo ficou aberto em
    outro terminal, o servidor de precos morria na largada ("Address already
    in use") e o navegador abria mesmo assim — caindo no servidor VELHO, que
    serve o painel mas nao tem a API de precos. Resultado: painel abre, porem
    com dados simulados. Desviar de porta resolve isso.
    """
    import socket

    for p in range(inicial, inicial + tentativas):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", p))
            return p
        except OSError:
            continue
        finally:
            try:
                s.close()
            except Exception:
                pass
    return -1


def _abrir_navegador(porta: int) -> None:
    import time
    import webbrowser

    def _vai():
        time.sleep(2.0)
        try:
            webbrowser.open(f"http://localhost:{porta}/painel.html")
            print(f"[servidor] navegador aberto em http://localhost:{porta}/painel.html")
        except Exception as exc:
            print(f"[servidor] nao abri o navegador ({exc}).")
            print(f"[servidor] Abra voce mesmo: http://localhost:{porta}/painel.html")

    threading.Thread(target=_vai, daemon=True).start()


def main() -> int:
    porta = 8000
    args = list(sys.argv[1:])
    abrir = "--sem-navegador" not in args
    for a in args:
        if a.isdigit():
            porta = int(a)
            break

    livre = _porta_livre(porta)
    if livre < 0:
        print(f"[servidor] ERRO: nenhuma porta livre entre {porta} e {porta + 24}.")
        return 1
    if livre != porta:
        print()
        print(f"  AVISO: a porta {porta} ja estava ocupada por outro programa")
        print(f"         (provavelmente um 'python -m http.server' antigo).")
        print(f"         Vou usar a porta {livre}.")
        print()
    porta = livre

    try:
        srv = ThreadingHTTPServer(("0.0.0.0", porta), Handler)
    except OSError as exc:
        print(f"[servidor] ERRO: nao consegui abrir a porta {porta}: {exc}")
        return 1

    try:
        (ROOT / "porta.txt").write_text(str(porta), encoding="utf-8")
    except Exception:
        pass

    print("=" * 62)
    print("  Painel de passagens — PRECOS REAIS (sem chave, sem custo)")
    print("=" * 62)
    print(f"  ENDERECO:  http://localhost:{porta}/painel.html")
    print("  (o navegador abre sozinho em alguns segundos)")
    print()
    print("  Se abrir com a faixa VERMELHA 'DADOS SIMULADOS',")
    print("  selecione todo o texto desta janela e me envie.")
    print("=" * 62)
    if abrir:
        _abrir_navegador(porta)
    mon_carregar()
    if MON.get("ativo") and MON.get("rotas"):
        mon_iniciar(MON["intervalo_min"], MON["rotas"])
        print(f"  Monitoramento retomado: a cada {MON['intervalo_min']} min "
              f"em {len(MON['rotas'])} rota(s)")
    print("=" * 62)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[servidor] encerrado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
