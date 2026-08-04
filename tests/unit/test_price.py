import pytest

from matching_engine.domain.price import (
    TICKS_PER_UNIT,
    InvalidPriceError,
    Ticks,
    format_price,
    parse_price,
)


@pytest.mark.parametrize("text", ["10", "9.99", "10.5", "0.01", "1000000"])
def test_roundtrip(text: str) -> None:
    assert format_price(parse_price(text)) == text


@pytest.mark.parametrize(
    ("text", "ticks"),
    [("10", 1000), ("10.5", 1050), ("9.99", 999), ("9.98", 998), ("0.01", 1)],
)
def test_parse_price_scales_by_tick(text: str, ticks: int) -> None:
    assert parse_price(text) == ticks


@pytest.mark.parametrize(
    ("ticks", "text"),
    [(1000, "10"), (1050, "10.5"), (999, "9.99"), (1, "0.01"), (100, "1"), (10, "0.1")],
)
def test_format_price_drops_trailing_zeros(ticks: int, text: str) -> None:
    assert format_price(Ticks(ticks)) == text


@pytest.mark.parametrize("text", ["10", "10.0", "10.00", "10.000", "10.0000000"])
def test_trailing_zeros_are_the_same_price(text: str) -> None:
    """Zeros além do tick são ruído de formatação, não precisão sub-tick."""
    assert parse_price(text) == 1000
    assert format_price(parse_price(text)) == "10"


def test_one_unit_is_one_tick_per_unit() -> None:
    assert parse_price("1") == TICKS_PER_UNIT


def test_addition_is_exact_unlike_float() -> None:
    """Documenta por que não usamos float: em binário, 0.1 + 0.2 != 0.3."""
    assert 0.1 + 0.2 != 0.3  # o contraste é o próprio ponto do teste
    assert parse_price("0.1") + parse_price("0.2") == parse_price("0.3")


@pytest.mark.parametrize(
    "text",
    [
        "0",
        "0.00",
        "-1",
        "1.234",
        "1.2340",
        "abc",
        "",
        "10.",
        ".5",
        "1e2",
        "NaN",
        "Infinity",
        " 10 ",
        "+1",
        "1,5",
        "10\n",
        "\n10",
        "1\n0",
        "10\r\n",
    ],
)
def test_parse_price_rejects(text: str) -> None:
    with pytest.raises(InvalidPriceError):
        parse_price(text)


def test_sub_tick_precision_is_reported_as_such() -> None:
    """O erro precisa dizer que o problema é o tick, não só que o preço é inválido."""
    with pytest.raises(InvalidPriceError, match=r"tick de 0\.01") as exc_info:
        parse_price("1.234")
    assert "1.234" in str(exc_info.value)


def test_invalid_price_is_a_value_error() -> None:
    assert issubclass(InvalidPriceError, ValueError)


def test_large_price_does_not_overflow() -> None:
    """int do Python é arbitrário: preço grande não perde precisão nem estoura."""
    text = "9" * 30 + ".99"
    ticks = parse_price(text)
    assert ticks == int("9" * 32)
    assert format_price(ticks) == text
