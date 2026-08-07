"""Os exemplos do enunciado, da linha digitada à linha impressa.

As três primeiras linhas do primeiro roteiro emitem ``Order created: ...``, que o enunciado
não mostra. Não é divergência acidental: é a consequência visível da decisão da ADR 0001 —
uma limit que repousa é um fato do livro e vira evento, e o enunciado só exibe a parte de
execução. O teste confere as linhas que de fato saem, e não as do enunciado literal; o que
precisa bater exatamente são as linhas de ``Trade``, que são a saída que o enunciado
especifica.

O mesmo vale, um passo adiante, para o roteiro do requisito 5: o enunciado exibe o **livro**
depois de cada comando, e não a linha de confirmação da ordem pegged, que ele não especifica
— ver ``presenter._pegged_line``. O que este teste fixa byte a byte, e é o que o enunciado
determina, são os quadros de ``print book``: a pegged entra atrás dos 200 @ 10 e, quando a
limit de 10.1 melhora o topo, ela sobe **acima** dela.

O lado vendedor do roteiro do requisito 5 não é enfeite de cenário: é ele que prova a
propriedade central da etapa. A pegged sobe de 10 para 10.1 com uma offer viva a 10.5, e
sai do comando sem ter executado nada — a reprecificação homolateral persegue o próprio
lado e nunca cruza o spread. Sem offer no livro não haveria o que cruzar, e o teste passaria
sem afirmar nada.
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

HEADER: Final = [
    "Ordens de Compra | Ordens de Venda",
    "-----------------|----------------",
]

PEGGED_SCRIPT: Final = [
    ("limit buy 10 200", ["Order created: buy 200 @ 10 1"]),
    # o segundo nível de compra: a pegged nunca desce até ele, e é essa a diferença entre
    # "está no melhor bid" e "está no único bid"
    ("limit buy 9.99 100", ["Order created: buy 100 @ 9.99 2"]),
    # a offer que fica de pé o roteiro inteiro, a 10.5, meio tick acima de tudo que o bid vai
    # alcançar
    ("limit sell 10.5 100", ["Order created: sell 100 @ 10.5 3"]),
    (
        "print book",
        [*HEADER, "200 @ 10         | 100 @ 10.5", "100 @ 9.99       |"],
    ),
    # sem preço próprio: ela assume o melhor bid não-pegged e entra ATRÁS dos 200 @ 10,
    # porque acabou de chegar
    ("peg bid buy 150", ["Order pegged: buy 150 @ 10 4"]),
    (
        "print book",
        [
            *HEADER,
            "200 @ 10         | 100 @ 10.5",
            "150 @ 10         |",
            "100 @ 9.99       |",
        ],
    ),
    # a limit nova melhora o topo do bid; a pegged o acompanha, no mesmo comando — e sobe
    # sem executar contra a offer de 10.5, porque perseguir o próprio lado nunca cruza
    (
        "limit buy 10.1 300",
        ["Order created: buy 300 @ 10.1 5", "Order pegged: buy 150 @ 10.1 4"],
    ),
    # e fica ACIMA da limit que provocou a mudança: ela já estava no livro, e reprecificar
    # não é reenviar — o sequence_id é preservado
    (
        "print book",
        [
            *HEADER,
            "150 @ 10.1       | 100 @ 10.5",
            "300 @ 10.1       |",
            "200 @ 10         |",
            "100 @ 9.99       |",
        ],
    ),
]


def test_the_statement_example_line_by_line() -> None:
    engine = MatchingEngine()
    cli = Cli(engine)

    for line, expected in SCRIPT:
        assert cli.execute(line) == expected, f"saída divergente para {line!r}"

    assert not cli.should_quit
    # o livro acabou vazio: tudo foi executado, e o que sobrou das markets foi descartado
    assert len(engine.book) == 0


def test_the_pegged_example_of_requirement_5_line_by_line() -> None:
    engine = MatchingEngine()
    cli = Cli(engine)

    for line, expected in PEGGED_SCRIPT:
        assert cli.execute(line) == expected, f"saída divergente para {line!r}"

    # as cinco ordens seguem vivas: nada executou, nem quando a pegged subiu para 10.1 com
    # uma offer a 10.5 do outro lado
    assert len(engine.book) == 5
    assert engine.book.best_bid == 1010
    assert engine.book.best_ask == 1050


def test_the_pegged_example_through_the_repl() -> None:
    """O mesmo roteiro pelo laço de leitura, para que o quadro do livro seja conferido tal
    como sai no terminal — com as quebras de linha e sem espaço invisível no fim."""
    lines = [line for line, _ in PEGGED_SCRIPT]
    stream_out = io.StringIO()

    run(Cli(MatchingEngine()), io.StringIO("\n".join([*lines, "quit", ""])), stream_out)

    written = _without_prompts(stream_out.getvalue())
    assert [line for line in written if line] == [
        line for _, expected in PEGGED_SCRIPT for line in expected
    ]
    # o quadro do livro é golden: espaço invisível no fim de uma linha quebra o teste sem
    # que a diferença apareça na tela
    assert all(line == line.rstrip() for line in written)


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
