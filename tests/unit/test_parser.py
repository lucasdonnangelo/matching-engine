import pytest

from matching_engine.domain.order import OrderId
from matching_engine.domain.price import Ticks
from matching_engine.domain.side import PegReference, Side
from matching_engine.io.commands import (
    CancelOrder,
    Command,
    ModifyOrder,
    PrintBook,
    Quit,
    SubmitLimit,
    SubmitMarket,
    SubmitPegged,
)
from matching_engine.io.parser import ParseError, parse


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "limit buy 10 100",
            SubmitLimit(side=Side.BUY, price=Ticks(1000), quantity=100),
        ),
        (
            "limit sell 10.5 250",
            SubmitLimit(side=Side.SELL, price=Ticks(1050), quantity=250),
        ),
        ("market buy 150", SubmitMarket(side=Side.BUY, quantity=150)),
        ("market sell 1", SubmitMarket(side=Side.SELL, quantity=1)),
        (
            "peg bid buy 100",
            SubmitPegged(reference=PegReference.BID, side=Side.BUY, quantity=100),
        ),
        (
            "peg offer sell 100",
            SubmitPegged(reference=PegReference.OFFER, side=Side.SELL, quantity=100),
        ),
        ("cancel order 7", CancelOrder(order_id=OrderId(7))),
        (
            "modify order 7 price 11 qty 20",
            ModifyOrder(order_id=OrderId(7), price=Ticks(1100), quantity=20),
        ),
        ("print book", PrintBook()),
        ("quit", Quit()),
        ("exit", Quit()),
    ],
)
def test_the_grammar(line: str, expected: Command) -> None:
    assert parse(line) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("LIMIT BUY 10 100", SubmitLimit(side=Side.BUY, price=Ticks(1000), quantity=100)),
        ("Market Sell 150", SubmitMarket(side=Side.SELL, quantity=150)),
        (
            "PEG Offer SELL 100",
            SubmitPegged(reference=PegReference.OFFER, side=Side.SELL, quantity=100),
        ),
        ("Cancel Order 7", CancelOrder(order_id=OrderId(7))),
        (
            "MODIFY ORDER 7 PRICE 11",
            ModifyOrder(order_id=OrderId(7), price=Ticks(1100), quantity=None),
        ),
        ("Print Book", PrintBook()),
        ("QUIT", Quit()),
        ("Exit", Quit()),
    ],
)
def test_keywords_ignore_case(line: str, expected: Command) -> None:
    assert parse(line) == expected


@pytest.mark.parametrize("line", ["", "   ", "\t", "\n", "  \t \n"])
def test_a_blank_line_is_not_a_command(line: str) -> None:
    """Enter num REPL não é engano a corrigir; o laço só volta a pedir entrada."""
    assert parse(line) is None


@pytest.mark.parametrize(
    "line",
    [
        "  limit buy 10 100",
        "limit buy 10 100   ",
        "limit   buy    10     100",
        "\tlimit\tbuy\t10\t100\t",
    ],
)
def test_spacing_is_not_syntax(line: str) -> None:
    assert parse(line) == SubmitLimit(side=Side.BUY, price=Ticks(1000), quantity=100)


@pytest.mark.parametrize("line", ["foo", "limitbuy 10 100", "book print", "order cancel 7"])
def test_unknown_command(line: str) -> None:
    with pytest.raises(ParseError, match="comando desconhecido"):
        parse(line)


@pytest.mark.parametrize(
    ("line", "received", "expected"),
    [
        ("limit", 0, 3),
        ("limit buy", 1, 3),
        ("limit buy 10", 2, 3),
        ("market", 0, 2),
        ("market buy", 1, 2),
        ("peg bid", 1, 3),
        ("peg bid buy", 2, 3),
        ("cancel", 0, 2),
        ("cancel order", 1, 2),
        ("print", 0, 1),
    ],
)
def test_missing_arguments(line: str, received: int, expected: int) -> None:
    """Não há token a apontar quando o que falta não foi escrito; informa-se a contagem."""
    with pytest.raises(ParseError) as raised:
        parse(line)

    message = str(raised.value)
    assert f"vieram {received} de {expected}" in message
    assert "forma esperada" in message


@pytest.mark.parametrize(
    ("line", "offending"),
    [
        ("limit buy 10 100 200", "200"),
        ("market buy 150 extra", "extra"),
        ("peg bid buy 100 extra", "extra"),
        ("cancel order 7 extra", "extra"),
        ("print book extra", "extra"),
        ("quit now", "now"),
        ("exit now", "now"),
    ],
)
def test_extra_arguments_name_the_offending_token(line: str, offending: str) -> None:
    """Sobra não é ignorada: a linha não conferida não vira ordem de verdade."""
    with pytest.raises(ParseError) as raised:
        parse(line)

    message = str(raised.value)
    assert repr(offending) in message
    assert "forma esperada" in message


def test_only_the_first_extra_token_is_named() -> None:
    """Os excedentes seguintes são consequência do primeiro, não erros independentes."""
    with pytest.raises(ParseError) as raised:
        parse("limit buy 10 100 200 300 400")

    message = str(raised.value)
    assert "'200'" in message
    assert "'300'" not in message
    assert "'400'" not in message


@pytest.mark.parametrize("line", ["limit up 10 100", "market hold 150", "peg bid hold 100"])
def test_invalid_side(line: str) -> None:
    with pytest.raises(ParseError, match="lado inválido"):
        parse(line)


@pytest.mark.parametrize("token", ["ask", "bids", "buy", "top"])
def test_invalid_peg_reference(token: str) -> None:
    with pytest.raises(ParseError, match="referência de peg inválida"):
        parse(f"peg {token} buy 100")


@pytest.mark.parametrize("token", ["0", "-5", "10.5", "abc", "1e3", "1_0", "+7"])
def test_invalid_quantity(token: str) -> None:
    with pytest.raises(ParseError, match="quantidade inválida"):
        parse(f"market buy {token}")


@pytest.mark.parametrize("token", ["0", "abc", "-1", "1e3", "10.", ".5", "1.234"])
def test_invalid_price_is_delegated_to_parse_price(token: str) -> None:
    """A mensagem vem de ``parse_price``, que distingue malformado de fora do tick."""
    with pytest.raises(ParseError) as raised:
        parse(f"limit buy {token} 100")

    assert "preço" in str(raised.value)


def test_price_finer_than_the_tick_keeps_the_original_message() -> None:
    with pytest.raises(ParseError, match="mais preciso que o tick"):
        parse("limit buy 1.234 100")


@pytest.mark.parametrize("token", ["abc", "0", "-1", "7.5"])
def test_invalid_order_id(token: str) -> None:
    with pytest.raises(ParseError, match="id de ordem inválido"):
        parse(f"cancel order {token}")


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "modify order 7 price 11",
            ModifyOrder(order_id=OrderId(7), price=Ticks(1100), quantity=None),
        ),
        (
            "modify order 7 qty 20",
            ModifyOrder(order_id=OrderId(7), price=None, quantity=20),
        ),
        (
            "modify order 7 price 11 qty 20",
            ModifyOrder(order_id=OrderId(7), price=Ticks(1100), quantity=20),
        ),
        (
            "modify order 7 qty 20 price 11",
            ModifyOrder(order_id=OrderId(7), price=Ticks(1100), quantity=20),
        ),
    ],
)
def test_modify_accepts_either_key_in_any_order(line: str, expected: ModifyOrder) -> None:
    assert parse(line) == expected


def test_modify_without_any_key_is_rejected() -> None:
    """Alteração que não altera nada é engano de digitação, não pedido a executar."""
    with pytest.raises(ParseError, match="ao menos price ou qty"):
        parse("modify order 7")


@pytest.mark.parametrize(
    "line",
    ["modify order 7 price 10 price 11", "modify order 7 qty 20 qty 30"],
)
def test_modify_rejects_a_repeated_key(line: str) -> None:
    """Contradição do usuário; escolher um dos dois em silêncio alteraria o que ele não pediu."""
    with pytest.raises(ParseError, match="chave repetida"):
        parse(line)


@pytest.mark.parametrize(
    "line",
    ["modify order 7 size 20", "modify order 7 price 10 side buy"],
)
def test_modify_rejects_an_unknown_key(line: str) -> None:
    with pytest.raises(ParseError, match="chave desconhecida"):
        parse(line)


@pytest.mark.parametrize("line", ["modify order 7 price", "modify order 7 qty 20 price"])
def test_modify_rejects_a_key_without_value(line: str) -> None:
    with pytest.raises(ParseError, match="falta o valor"):
        parse(line)


def test_modify_needs_the_order_keyword() -> None:
    with pytest.raises(ParseError, match="esperado 'order'"):
        parse("modify 7 qty 20")


@pytest.mark.parametrize("line", ["cancel pedido 7", "print livro"])
def test_the_fixed_words_of_the_grammar_are_required(line: str) -> None:
    with pytest.raises(ParseError, match="esperado"):
        parse(line)


def test_parse_error_is_a_value_error() -> None:
    """Seção 6 do contrato: entrada inválida do usuário, não inconsistência interna."""
    assert issubclass(ParseError, ValueError)
    assert not issubclass(ParseError, RuntimeError)
