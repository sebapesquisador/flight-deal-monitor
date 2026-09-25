#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diagnostico: descobre por que o painel fica em modo simulado.

Nunca quebra: qualquer falha vira uma linha do relatorio.
Rode pelo DIAGNOSTICO.bat (que salva tudo em diagnostico.txt).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import traceback

LIN: list[str] = []


def w(txt: str = "") -> None:
    print(txt)
    LIN.append(txt)


def sec(titulo: str) -> None:
    w()
    w("=" * 62)
    w("  " + titulo)
    w("=" * 62)


def roda(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        saida = (p.stdout or "") + (p.stderr or "")
        return p.returncode, saida.strip()
    except FileNotFoundError:
        return 127, "arquivo nao encontrado"
    except subprocess.TimeoutExpired:
        return -1, f"timeout de {timeout}s"
    except Exception as exc:
        return -2, f"{type(exc).__name__}: {exc}"


def main() -> int:
    sec("DIAGNOSTICO - MONITOR DE PASSAGENS")
    w(f"  data/hora : {__import__('datetime').datetime.now():%d/%m/%Y %H:%M:%S}")
    w(f"  pasta     : {os.getcwd()}")
    w(f"  python    : {sys.executable}")
    w(f"  versao    : {sys.version.split()[0]}  ({sys.platform})")

    sec("1) ARQUIVOS DA PASTA")
    for nome in ("painel.html", "servidor.py", "INICIAR.bat", "LEIA-ME.txt"):
        w(f"  {'OK  ' if os.path.isfile(nome) else 'FALTA'} {nome}")

    sec("2) PACOTE fast-flights (conector de precos)")
    try:
        import fast_flights  # noqa: F401
        w("  OK - fast_flights instalado")
        try:
            import importlib.metadata as md
            w(f"  versao: {md.version('fast-flights')}")
        except Exception:
            pass
    except Exception as exc:
        w(f"  FALHA - {type(exc).__name__}: {exc}")
        w("  (sem isso o painel so mostra dados simulados)")

    sec("3) INTERNET")
    try:
        socket.create_connection(("pypi.org", 443), timeout=15).close()
        w("  OK - consegui alcancar pypi.org")
    except Exception as exc:
        w(f"  FALHA - {type(exc).__name__}: {exc}")

    sec("4) PORTA 8000 (servidor)")
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 8000))
        w("  OK - porta 8000 livre")
    except Exception as exc:
        w(f"  OCUPADA - {type(exc).__name__}: {exc}")
        w("  (pode ser um servidor antigo ainda aberto)")
    finally:
        try:
            s.close()
        except Exception:
            pass

    sec("5) TESTE REAL DE PRECO (GRU -> REC, 01/10/2026)")
    try:
        from fast_flights import (FlightQuery, Passengers, create_filter,
                                  get_flights)

        q = create_filter(
            flights=[FlightQuery(date="2026-10-01", from_airport="GRU",
                                 to_airport="REC")],
            trip="one-way", seat="economy", passengers=Passengers(adults=1),
            language="pt-BR", currency="BRL",
        )
        res = get_flights(q)
        precos = sorted(f.price for f in res)
        if precos:
            w(f"  OK - {len(precos)} voos reais encontrados")
            w(f"  menor preco: R$ {precos[0]:,.2f}".replace(",", "X")
              .replace(".", ",").replace("X", "."))
            w(f"  maior preco: R$ {precos[-1]:,.2f}".replace(",", "X")
              .replace(".", ",").replace("X", "."))
            w("  >>> O CONECTOR FUNCIONA. O problema e outro. <<<")
        else:
            w("  ATENCAO - a consulta voltou vazia (Google pode ter bloqueado)")
    except Exception as exc:
        w(f"  FALHA - {type(exc).__name__}: {exc}")
        w("  detalhe:")
        for lin in traceback.format_exc().strip().splitlines()[-8:]:
            w("    " + lin)

    sec("6) PIP (gerenciador de pacotes)")
    cod, saida = roda([sys.executable, "-m", "pip", "--version"], timeout=60)
    w(f"  codigo={cod}")
    w("  " + (saida or "(sem resposta)").replace("\n", "\n  "))

    sec("FIM DO RELATORIO")
    w("  Envie este arquivo (diagnostico.txt) para a analise.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
