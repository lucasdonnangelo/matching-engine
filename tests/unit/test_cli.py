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

    O dublê existe porque as duas metades da taxonomia da seção 6 do contrato precisam ser
    exercitadas na fronteira, e nenhuma linha de entrada as alcança hoje. ``ValueError`` do
    domínio: o parser recusa antes tudo que viraria ``InvalidOrderError`` — quantidade não
    positiva, preço fora do tick — e o peg cruzado, que é o outro caminho, para no despacho
    de comando não implementado. ``RuntimeError``: só aparece quando a engine perde a conta
    das próprias ordens, que é por definição o que não se provoca de fora.

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


@pytest.mark.parametrize(
    ("line", "name"),
    [
        ("peg bid buy 100", "peg"),
        # o peg cruzado também para aqui: a lateralidade é do domínio, que ainda não é
        # alcançado por este comando
        ("peg bid sell 100", "peg"),
        ("cancel order 1", "cancel order"),
        ("modify order 1 qty 5", "modify order"),
        ("modify order 1 price 10", "modify order"),
        ("print book", "print book"),
    ],
)
def test_the_commands_that_are_not_implemented_yet_name_themselves(line: str, name: str) -> None:
    cli = Cli(MatchingEngine())

    assert cli.execute(line) == [f"Error: comando ainda não implementado: {name}"]


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
