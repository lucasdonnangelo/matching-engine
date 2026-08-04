"""O exemplo principal do enunciado, da linha digitada à linha impressa.

As três primeiras linhas emitem ``Order created: ...``, que o enunciado não mostra. Não é
divergência acidental: é a consequência visível da decisão da ADR 0001 — uma limit que
repousa é um fato do livro e vira evento, e o enunciado só exibe a parte de execução.
O teste confere as linhas que de fato saem, e não as do enunciado literal; o que precisa
bater exatamente são as linhas de ``Trade``, que são a saída que o enunciado especifica.
"""

import io
from typing import Final

from matching_engine.domain.engine import MatchingEngine
from matching_engine.io.cli import PROMPT, Cli, run

SCRIPT: Final = [
    ("limit buy 10 100", ["Order created: buy 100 @ 10 1"]),
    ("limit sell 20 100", ["Order created: sell 100 @ 20 2"]),
    ("limit sell 20 200", ["Order created: sell 200 @ 20 3"]),
    # 150 contra 100 @ 20 e 200 @ 20: dois Trades no domínio, uma linha na saída
    ("market buy 150", ["Trade, price: 20, qty: 150"]),
    # só restam 150 na offer; os outros 50 da market somem, sem repousar (IOC)
    ("market buy 200", ["Trade, price: 20, qty: 150"]),
    # a offer acabou; a venda atinge o bid de 10 e os 100 restantes somem
    ("market sell 200", ["Trade, price: 10, qty: 100"]),
]

EXPECTED_LINES: Final = [line for _, lines in SCRIPT for line in lines]


def test_the_statement_example_line_by_line() -> None:
    engine = MatchingEngine()
    cli = Cli(engine)

    for line, expected in SCRIPT:
        assert cli.execute(line) == expected, f"saída divergente para {line!r}"

    assert not cli.should_quit
    # o livro acabou vazio: tudo foi executado, e o que sobrou das markets foi descartado
    assert len(engine.book) == 0


def test_the_same_session_through_the_repl_ending_in_quit() -> None:
    stream_in = io.StringIO("\n".join([line for line, _ in SCRIPT] + ["quit", ""]))
    stream_out = io.StringIO()

    run(Cli(MatchingEngine()), stream_in, stream_out)

    printed = stream_out.getvalue()
    assert [line for line in _without_prompts(printed) if line] == EXPECTED_LINES
    # um prompt por linha lida, inclusive a do quit
    assert printed.count(PROMPT) == len(SCRIPT) + 1


def test_the_same_session_ending_in_eof() -> None:
    """Entrada redirecionada acaba sem digitar quit; travar esperando o que não vem é pior."""
    stream_in = io.StringIO("\n".join(line for line, _ in SCRIPT) + "\n")
    stream_out = io.StringIO()

    run(Cli(MatchingEngine()), stream_in, stream_out)

    printed = stream_out.getvalue()
    assert [line for line in _without_prompts(printed) if line] == EXPECTED_LINES
    # o prompt sem resposta do EOF, e a quebra que devolve o cursor ao shell
    assert printed.count(PROMPT) == len(SCRIPT) + 1
    assert printed.endswith(f"{PROMPT}\n")


def _without_prompts(printed: str) -> list[str]:
    return [line.removeprefix(PROMPT) for line in printed.splitlines()]
