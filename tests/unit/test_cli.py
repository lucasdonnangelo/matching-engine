import io

import pytest

from matching_engine.domain.engine import MatchingEngine
from matching_engine.domain.events import Event
from matching_engine.domain.order import InvalidOrderError
from matching_engine.domain.order_queue import QueueIntegrityError
from matching_engine.domain.price import InvalidPriceError, Ticks, parse_price
from matching_engine.domain.side import Side
from matching_engine.io.cli import PROMPT, Cli, run
from matching_engine.io.parser import ParseError, parse


class InterruptedInput(io.StringIO):
    """Entrada que recebe Ctrl+C: ``readline`` levanta onde o terminal levantaria.

    Num terminal, o SIGINT chega durante a leitura bloqueante e vira ``KeyboardInterrupt``
    de dentro do ``readline``. A classe reproduz esse ponto exato, depois de deixar passar
    as linhas que o usuário já tinha digitado.
    """

    def __init__(self, text: str, interrupt_after: int) -> None:
        super().__init__(text)
        self._remaining = interrupt_after

    def readline(self, size: int | None = -1) -> str:
        if self._remaining == 0:
            raise KeyboardInterrupt
        self._remaining -= 1
        return super().readline()


class FailingEngine(MatchingEngine):
    """Engine que falha no ``submit_limit`` com a exceção pedida.

    O dublê existe porque a metade ``RuntimeError`` da taxonomia da seção 6 do contrato
    precisa ser exercitada na fronteira, e nenhuma linha de entrada a alcança: ela só aparece
    quando a engine perde a conta das próprias ordens, que é por definição o que não se
    provoca de fora.

    A metade ``ValueError`` é exercitada aqui pelo mesmo dublê, mas não depende dele: o peg
    cruzado chega ao domínio por uma linha de verdade — ver o teste da linha de erro do peg
    cruzado. O que continua sem caminho de entrada é o resto de ``InvalidOrderError``, porque
    o parser recusa antes tudo que viraria isso: quantidade não positiva, preço fora do tick.

    Herda de ``MatchingEngine`` em vez de imitar a interface para que o ``Cli`` receba o
    tipo que ele de fato espera, e para que o dia em que ele passar a chamar outro método
    da engine o dublê continue respondendo de verdade.
    """

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    def submit_limit(self, side: Side, price: Ticks, quantity: int) -> list[Event]:
        raise self._error


def test_eof_on_an_empty_session_ends_cleanly() -> None:
    """Entrada exaurida encerra sem exceção; sobra o prompt sem resposta e a quebra."""
    stream_out = io.StringIO()

    run(Cli(MatchingEngine()), io.StringIO(""), stream_out)

    assert stream_out.getvalue() == f"{PROMPT}\n"


def test_eof_after_a_command_ends_cleanly() -> None:
    stream_out = io.StringIO()

    run(Cli(MatchingEngine()), io.StringIO("limit buy 10 100\n"), stream_out)

    assert stream_out.getvalue() == f"{PROMPT}Order created: buy 100 @ 10 1\n{PROMPT}\n"


def test_ctrl_c_ends_like_eof_ends() -> None:
    """Ctrl+C é encerramento normal: mesma saída do EOF, sem traceback subindo."""
    stream_in = InterruptedInput("limit buy 10 100\n", interrupt_after=1)
    stream_out = io.StringIO()

    run(Cli(MatchingEngine()), stream_in, stream_out)

    assert stream_out.getvalue() == f"{PROMPT}Order created: buy 100 @ 10 1\n{PROMPT}\n"


def test_ctrl_c_before_typing_anything_ends_cleanly() -> None:
    stream_out = io.StringIO()

    run(Cli(MatchingEngine()), InterruptedInput("", interrupt_after=0), stream_out)

    assert stream_out.getvalue() == f"{PROMPT}\n"


def test_a_valid_command_returns_the_presenter_lines() -> None:
    cli = Cli(MatchingEngine())

    assert cli.execute("limit buy 10 100") == ["Order created: buy 100 @ 10 1"]
    assert cli.execute("limit sell 20 100") == ["Order created: sell 100 @ 20 2"]
    assert cli.execute("market buy 60") == ["Trade, price: 20, qty: 60"]


def test_a_blank_line_produces_no_output() -> None:
    cli = Cli(MatchingEngine())

    assert cli.execute("") == []
    assert cli.execute("   \n") == []


def test_a_parse_error_becomes_one_error_line_with_the_original_message() -> None:
    """A mensagem é comparada com a que o parser produz, e não com uma cópia literal."""
    cli = Cli(MatchingEngine())

    with pytest.raises(ParseError) as raised:
        parse("foo bar")
    lines = cli.execute("foo bar")

    assert lines == [f"Error: {raised.value}"]
    assert len(lines) == 1
    assert lines[0].startswith("Error: ")


def test_an_invalid_price_becomes_an_error_line_with_the_domain_message() -> None:
    """O preço fora do tick é recusado por ``parse_price``, no domínio.

    O ``ParseError`` que chega à fronteira é reembalagem — o parser troca o tipo e preserva
    o texto —, então o que a linha de erro mostra é a mensagem escrita em ``price.py``, que
    distingue preço malformado de preço mais preciso que o tick.
    """
    cli = Cli(MatchingEngine())

    with pytest.raises(InvalidPriceError) as raised:
        parse_price("1.234")

    assert cli.execute("limit buy 1.234 100") == [f"Error: {raised.value}"]


@pytest.mark.parametrize(
    "error",
    [
        InvalidOrderError("quantidade deve ser maior que zero: 0"),
        InvalidOrderError("peg cruzado: referência BID não acompanha o lado SELL"),
        InvalidPriceError("preço deve ser maior que zero: '0'"),
    ],
)
def test_a_value_error_from_the_domain_becomes_an_error_line(error: Exception) -> None:
    """A fronteira trata a família ``ValueError`` inteira, sem enumerar exceção por exceção."""
    cli = Cli(FailingEngine(error))

    assert cli.execute("limit buy 10 100") == [f"Error: {error}"]


def test_an_internal_runtime_error_is_not_swallowed() -> None:
    """Inconsistência interna estoura alto: trocar o ``except`` por ``Exception`` quebra aqui.

    Capturar ``RuntimeError`` faria o REPL seguir executando ordens contra um livro que já
    não é confiável, imprimindo ``Error:`` como se a culpa fosse de quem digitou.
    """
    cli = Cli(FailingEngine(QueueIntegrityError("ordem 1 não pertence a esta fila")))

    with pytest.raises(QueueIntegrityError, match="não pertence a esta fila"):
        cli.execute("limit buy 10 100")


def test_every_command_of_the_grammar_reaches_the_engine() -> None:
    """Nenhum comando fica sem implementação: a gramática inteira responde de verdade."""
    cli = Cli(MatchingEngine())

    assert cli.execute("limit buy 10 100") == ["Order created: buy 100 @ 10 1"]
    assert cli.execute("peg bid buy 50") == ["Order pegged: buy 50 @ 10 2"]
    # a market atinge a limit, que está na frente da fila; a pegged segue intocada atrás dela
    assert cli.execute("market sell 20") == ["Trade, price: 10, qty: 20"]
    # 100 com 20 executados, reduzida para 60: sobram 40 de saldo
    assert cli.execute("modify order 1 qty 60") == ["Order amended: buy 40 @ 10 1"]
    assert cli.execute("cancel order 2") == ["Order cancelled"]
    assert cli.execute("print book") == [
        "Ordens de Compra | Ordens de Venda",
        "-----------------|----------------",
        "40 @ 10          |",
    ]
    assert cli.execute("quit") == []


@pytest.mark.parametrize("line", ["peg bid sell 100", "peg offer buy 100"])
def test_a_crossed_peg_is_an_error_line_from_the_domain(line: str) -> None:
    """A lateralidade é regra da ``Order``, e a linha de erro é a mensagem dela, sem reembalar."""
    cli = Cli(MatchingEngine())

    lines = cli.execute(line)

    assert len(lines) == 1
    assert lines[0].startswith("Error: peg cruzado: ")
    assert not cli.should_quit


def test_create_then_cancel_as_the_statement_shows() -> None:
    cli = Cli(MatchingEngine())

    assert cli.execute("limit buy 10 100") == ["Order created: buy 100 @ 10 1"]
    assert cli.execute("cancel order 1") == ["Order cancelled"]
    assert cli.execute("print book") == [
        "Ordens de Compra | Ordens de Venda",
        "-----------------|----------------",
    ]


def test_create_modify_and_print_book_as_the_statement_shows() -> None:
    """O requisito 4: a ordem de 200 desce para 9.98 e passa a figurar abaixo da de 9.99."""
    cli = Cli(MatchingEngine())

    assert cli.execute("limit buy 10 200") == ["Order created: buy 200 @ 10 1"]
    assert cli.execute("limit buy 9.99 100") == ["Order created: buy 100 @ 9.99 2"]
    assert cli.execute("modify order 1 price 9.98") == ["Order amended: buy 200 @ 9.98 1"]
    assert cli.execute("print book") == [
        "Ordens de Compra | Ordens de Venda",
        "-----------------|----------------",
        "100 @ 9.99       |",
        "200 @ 9.98       |",
    ]


def test_modifying_the_quantity_keeps_the_order_at_the_front_of_the_book() -> None:
    cli = Cli(MatchingEngine())
    for line in ["limit sell 20 100", "limit sell 20 200"]:
        cli.execute(line)

    assert cli.execute("modify order 1 qty 40") == ["Order amended: sell 40 @ 20 1"]
    assert cli.execute("print book") == [
        "Ordens de Compra | Ordens de Venda",
        "-----------------|----------------",
        "                 | 40 @ 20",
        "                 | 200 @ 20",
    ]


def test_a_modify_that_crosses_the_book_trades_from_the_repl() -> None:
    cli = Cli(MatchingEngine())
    cli.execute("limit sell 20 100")
    cli.execute("limit buy 10 150")

    assert cli.execute("modify order 2 price 20") == [
        "Trade, price: 20, qty: 100",
        "Order amended: buy 50 @ 20 2",
    ]


def test_modifying_an_unknown_id_is_an_error_line() -> None:
    cli = Cli(MatchingEngine())

    lines = cli.execute("modify order 999 qty 5")

    assert len(lines) == 1
    assert lines[0].startswith("Error: ")
    assert "999" in lines[0]


def test_cancelling_an_unknown_id_is_an_error_line() -> None:
    cli = Cli(MatchingEngine())

    lines = cli.execute("cancel order 999")

    assert len(lines) == 1
    assert lines[0].startswith("Error: ")
    assert "999" in lines[0]


def test_print_book_shows_the_book() -> None:
    cli = Cli(MatchingEngine())
    for line in ["limit buy 10 200", "limit buy 9.99 100", "limit sell 10.5 100"]:
        cli.execute(line)

    assert cli.execute("print book") == [
        "Ordens de Compra | Ordens de Venda",
        "-----------------|----------------",
        "200 @ 10         | 100 @ 10.5",
        "100 @ 9.99       |",
    ]


def test_print_book_on_an_empty_book_still_shows_the_header() -> None:
    assert Cli(MatchingEngine()).execute("print book") == [
        "Ordens de Compra | Ordens de Venda",
        "-----------------|----------------",
    ]


@pytest.mark.parametrize("line", ["quit", "exit", "QUIT"])
def test_quit_returns_no_lines_and_flips_should_quit(line: str) -> None:
    cli = Cli(MatchingEngine())
    assert not cli.should_quit

    assert cli.execute(line) == []
    assert cli.should_quit


def test_an_error_line_does_not_end_the_session() -> None:
    """Erro de usuário é resposta, não encerramento: o comando seguinte executa normalmente."""
    cli = Cli(MatchingEngine())

    assert cli.execute("foo bar")[0].startswith("Error: ")

    assert not cli.should_quit
    assert cli.execute("limit buy 10 100") == ["Order created: buy 100 @ 10 1"]
