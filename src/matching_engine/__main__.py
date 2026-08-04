"""Ponto de entrada: ``python -m matching_engine``.

Só monta as peças e liga os streams. Toda decisão está do lado de dentro — se este arquivo
crescer, é sinal de que alguma delas vazou para cá.
"""

from __future__ import annotations

import io
import sys
from typing import cast

from matching_engine.domain.engine import MatchingEngine
from matching_engine.io.cli import Cli, run


def main() -> None:
    # O encoding de stdio depende da plataforma e de a saída estar ou não redirecionada:
    # no Windows, pipe ou arquivo caem em cp1252 e as mensagens acentuadas corrompem.
    # Fixar UTF-8 torna a saída determinística, que é do que os testes golden dependem.
    # ``reconfigure`` mora em ``TextIOWrapper``, e ``sys.stdin``/``sys.stdout`` são
    # tipados como ``TextIO``: o cast declara o que eles de fato são, em vez de calar o
    # mypy com um ignore.
    cast(io.TextIOWrapper, sys.stdin).reconfigure(encoding="utf-8")
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding="utf-8")

    run(Cli(MatchingEngine()), sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
