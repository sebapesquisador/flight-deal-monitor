"""Wrapper de conveniencia: permite rodar de qualquer diretorio.

    python fdm.py demo
    python fdm.py api

Mesmo assim, prefira executar dentro da pasta do projeto (o SQLite e criado
em ./data relativo ao diretorio atual).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
