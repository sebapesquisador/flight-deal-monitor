#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Repara sozinho as dependencias do conector de precos.

Problema que este arquivo resolve: o pacote fast-flights 3.1.0 esqueceu
de declarar que precisa de `typing_extensions`. O `pip install fast-flights`
portanto diz "Requirement already satisfied" e mesmo assim o import quebra
com "No module named 'typing_extensions'".

Estrategia: tenta importar cada modulo; para cada falha, extrai o nome do
modulo que faltou e roda `pip install` nele; depois re-testa. O laco
suporta dependencias em cascata (um modulo faltando revela o proximo).

Uso:  python consertar.py
"""
from __future__ import annotations

import re
import subprocess
import sys

# modulos que o painel precisa, na ordem de verificacao
MODULOS = ("primp", "selectolax", "google.protobuf", "typing_extensions", "fast_flights")

# nome do modulo -> nome do pacote no PyPI (quando sao diferentes)
PACOTE = {
    "google.protobuf": "protobuf",
    "google": "protobuf",
    "fast_flights": "fast-flights",
}

MAX_RODADAS = 5


def _pip(pacote: str) -> tuple[int, str]:
    """Roda `python -m pip install -U <pacote>` e devolve (codigo, saida)."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", pacote],
            capture_output=True, text=True, timeout=600,
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def _pacote_para(nome_modulo: str) -> str:
    # o erro de "google.protobuf" vem como "No module named 'google'";
    # instalar o pacote "google" do PyPI seria ERRADO (e um shim inutil)
    if nome_modulo == "google" or nome_modulo.startswith("google."):
        return "protobuf"
    if nome_modulo in PACOTE:
        return PACOTE[nome_modulo]
    if nome_modulo.startswith("fast_flights"):
        return "fast-flights"
    return nome_modulo.split(".")[0]


def _faltando() -> list[tuple[str, str]]:
    """Devolve [(modulo, erro)] para tudo que nao importa."""
    fora = []
    for m in MODULOS:
        try:
            __import__(m)
        except Exception as exc:
            fora.append((m, str(exc)))
    return fora


def _alvos(faltando: list[tuple[str, str]]) -> set[str]:
    """Extrai do texto de erro os pacotes que precisam ser instalados."""
    alvos: set[str] = set()
    for modulo, erro in faltando:
        achou = re.search(r"No module named '([^']+)'", erro)
        if achou:
            alvos.add(_pacote_para(achou.group(1)))
        else:
            # quebrou por outro motivo (versao, binario) -> reinstala o pacote
            alvos.add(_pacote_para(modulo))
    return alvos


def main() -> int:
    print()
    print("=" * 66)
    print("  REPARO AUTOMATICO DAS DEPENDENCIAS")
    print("=" * 66)
    print(f"  Python: {sys.executable}")

    instalados: set[str] = set()

    for rodada in range(1, MAX_RODADAS + 1):
        faltando = _faltando()
        if not faltando:
            print()
            print("  Tudo instalado e funcionando.")
            print("=" * 66)
            print()
            return 0

        alvos = _alvos(faltando) - instalados
        if not alvos:
            print()
            print("  Nao consegui resolver sozinho. Erros restantes:")
            for modulo, erro in faltando:
                print(f"    {modulo}: {erro}")
            print("=" * 66)
            print()
            return 1

        print(f"\n  Rodada {rodada}: instalando {', '.join(sorted(alvos))}")
        for alvo in sorted(alvos):
            instalados.add(alvo)
            print(f"    pip install -U {alvo} ...", end="", flush=True)
            codigo, saida = _pip(alvo)
            if codigo == 0:
                print(" ok")
            else:
                print(" FALHOU")
                for linha in saida.strip().splitlines()[-8:]:
                    print(f"      {linha.strip()}")

    faltando = _faltando()
    print()
    if faltando:
        print("  Ainda faltam:")
        for modulo, erro in faltando:
            print(f"    {modulo}: {erro}")
    print("=" * 66)
    print()
    return 0 if not faltando else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
