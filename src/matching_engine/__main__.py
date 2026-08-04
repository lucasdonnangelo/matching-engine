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
    #
    # ``newline`` completa o par. No Windows o stdout traduz ``\n`` para ``\r\n``, de modo
    # que a mesma sessão produz bytes diferentes conforme a plataforma. Os testes usam
    # ``StringIO``, que não traduz nada, então a divergência não apareceria neles — só ao
    # comparar saída redirecionada, com a suíte verde no Windows e vermelha na CI Linux.
    # É a assinatura de bug de line ending que o ``.gitattributes`` existe para evitar nos
    # arquivos, e que aqui precisa ser evitada na saída do processo.
    #
    # ``sys.stdin`` fica só com o encoding: a tradução na leitura é benigna, porque o
    # parser separa a linha por espaço e a quebra do fim some junto.
    #
    # ``reconfigure`` mora em ``TextIOWrapper``, e ``sys.stdin``/``sys.stdout`` são
    # tipados como ``TextIO``: o cast declara o que eles de fato são, em vez de calar o
    # mypy com um ignore.
    cast(io.TextIOWrapper, sys.stdin).reconfigure(encoding="utf-8")
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding="utf-8", newline="\n")

    run(Cli(MatchingEngine()), sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
