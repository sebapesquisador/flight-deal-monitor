#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verifica se o conector de precos carrega — e mostra o erro real se nao.

Diferenca importante: `pip install` dizer "Requirement already satisfied"
NAO significa que o pacote funciona. primp e selectolax sao extensoes
compiladas; no Windows elas falham por falta do Visual C++ Redistributable.
A unica forma de saber e tentar importar e olhar o traceback completo.

Uso:  python verificar.py
"""
from __future__ import annotations

import sys
import traceback

MODULOS = ("primp", "selectolax", "google.protobuf", "fast_flights")


def _erro_de(nome: str):
    """Devolve a excecao ao importar `nome`, ou None se importa bem."""
    try:
        __import__(nome)
        return None
    except Exception as exc:
        return exc


def main() -> int:
    print()
    print("=" * 66)
    print("  VERIFICACAO DO CONECTOR DE PRECOS")
    print("=" * 66)
    print(f"  Python {sys.version.split()[0]}")
    print(f"  Executavel: {sys.executable}")
    print()

    falhas = 0
    for nome in MODULOS:
        try:
            mod = __import__(nome)
            v = getattr(mod, "__version__", "")
            print(f"  [OK]    {nome}" + (f"  (versao {v})" if v else ""))
        except Exception as exc:
            falhas += 1
            print(f"  [FALHA] {nome}")
            print(f"          {type(exc).__name__}: {exc}")
            tb = traceback.format_exc().strip().splitlines()
            for lin in tb[-4:]:
                print(f"          {lin.strip()}")
            print()

    print("-" * 66)
    if falhas == 0:
        # teste de verdade: uma consulta real
        print("  Todos os modulos carregaram. Testando uma consulta real...")
        try:
            from datetime import date

            import importlib.util
            from pathlib import Path

            spec = importlib.util.spec_from_file_location(
                "srv", Path(__file__).resolve().parent / "servidor.py")
            srv = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(srv)
            r = srv._chamar("GRU", "REC", date(2026, 10, 1), None, "economy")
            if r:
                m = min(r, key=lambda x: x["preco"])
                print(f"  CONSULTA OK: {len(r)} voos, menor R$ {m['preco']:,.2f} ({m['cia']})")
                print()
                print("  >>> TUDO FUNCIONANDO. O painel deve abrir com precos reais. <<<")
            else:
                print("  A consulta voltou vazia (o Google pode ter bloqueado).")
        except Exception:
            print("  Falha na consulta de teste:")
            traceback.print_exc()
    else:
        print(f"  {falhas} modulo(s) com problema.")
        print()
        txt = " ".join(
            f"{type(e).__name__}: {e}" for m in MODULOS
            for e in [None] if False)  # placeholder, recalculado abaixo
        erros = " ".join(
            (f"{type(exc).__name__}: {exc}".lower())
            for m in MODULOS
            for exc in [_erro_de(m)] if exc)
        if "no module named" in erros:
            print("  CAUSA: falta um pacote que o fast-flights NAO declara.")
            print("    O INICIAR.bat ja tenta instalar sozinho.")
            print("    Se quiser fazer a mao, no terminal do VSCode:")
            print("      pip install typing_extensions")
        elif "dll load failed" in erros or "could not be found" in erros:
            print("  CAUSA: Visual C++ Redistributable ausente.")
            print("    Baixe e instale:")
            print("    https://aka.ms/vs/17/release/vc_redist.x64.exe")
            print("    Depois feche e abra o INICIAR.bat de novo.")
        else:
            print("  CAUSA: ainda nao identificada.")
            print("    Me envie este texto inteiro que eu resolvo.")
        print()
    print("=" * 66)
    print()
    return 0 if falhas == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
