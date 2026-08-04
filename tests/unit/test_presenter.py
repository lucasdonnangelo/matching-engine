import pytest

from matching_engine.domain.events import Event, OrderAccepted, Trade
from matching_engine.domain.order import OrderId
from matching_engine.domain.price import Ticks
from matching_engine.domain.side import Side
from matching_engine.io.presenter import format_events


def trade(price: int, quantity: int, maker: int = 1, taker_side: Side = Side.BUY) -> Trade:
    return Trade(
        price=Ticks(price),
        quantity=quantity,
        maker_order_id=OrderId(maker),
        taker_side=taker_side,
    )


def accepted(price: int, quantity: int, side: Side = Side.BUY, order_id: int = 1) -> OrderAccepted:
    return OrderAccepted(
        order_id=OrderId(order_id), side=side, price=Ticks(price), quantity=quantity
    )


def test_no_events_no_lines() -> None:
    assert format_events([]) == []


def test_a_single_trade() -> None:
    assert format_events([trade(2000, 150)]) == ["Trade, price: 20, qty: 150"]


@pytest.mark.parametrize(
    ("side", "expected"),
    [
        (Side.BUY, "Order created: buy 100 @ 10 7"),
        (Side.SELL, "Order created: sell 100 @ 10 7"),
    ],
)
def test_an_accepted_order_prints_the_side_in_lower_case(side: Side, expected: str) -> None:
    assert format_events([accepted(1000, 100, side=side, order_id=7)]) == [expected]


@pytest.mark.parametrize(
    ("ticks", "expected"),
    [
        (Ticks(1000), "Trade, price: 10, qty: 5"),
        (Ticks(1050), "Trade, price: 10.5, qty: 5"),
        (Ticks(998), "Trade, price: 9.98, qty: 5"),
        (Ticks(100), "Trade, price: 1, qty: 5"),
        (Ticks(1), "Trade, price: 0.01, qty: 5"),
    ],
)
def test_prices_are_printed_without_trailing_zeros(ticks: Ticks, expected: str) -> None:
    assert format_events([trade(ticks, 5)]) == [expected]


def test_consecutive_trades_at_the_same_price_become_one_line() -> None:
    """O exemplo do enunciado: 100 @ 20 e 200 @ 20 atingidos por um market buy 150."""
    events: list[Event] = [trade(2000, 100, maker=1), trade(2000, 50, maker=2)]

    assert format_events(events) == ["Trade, price: 20, qty: 150"]


def test_trades_at_different_prices_stay_apart() -> None:
    events: list[Event] = [trade(2000, 100), trade(2100, 50)]

    assert format_events(events) == [
        "Trade, price: 20, qty: 100",
        "Trade, price: 21, qty: 50",
    ]


def test_a_price_that_comes_back_is_a_third_line() -> None:
    """A, B, A são três passagens pelo livro; somar as duas A reordenaria a execução."""
    events: list[Event] = [trade(2000, 10), trade(2100, 20), trade(2000, 30)]

    assert format_events(events) == [
        "Trade, price: 20, qty: 10",
        "Trade, price: 21, qty: 20",
        "Trade, price: 20, qty: 30",
    ]


def test_aggregation_does_not_cross_another_event() -> None:
    """Entre dois trades de mesmo preço houve uma ordem aceita, e a cronologia relata isso."""
    events: list[Event] = [
        trade(2000, 10),
        accepted(2000, 40, side=Side.SELL, order_id=3),
        trade(2000, 20),
    ]

    assert format_events(events) == [
        "Trade, price: 20, qty: 10",
        "Order created: sell 40 @ 20 3",
        "Trade, price: 20, qty: 20",
    ]


def test_a_marketable_limit_prints_its_fills_and_then_its_remainder() -> None:
    events: list[Event] = [
        trade(2000, 100, maker=1),
        trade(2000, 50, maker=2),
        accepted(2000, 50, side=Side.BUY, order_id=3),
    ]

    assert format_events(events) == [
        "Trade, price: 20, qty: 150",
        "Order created: buy 50 @ 20 3",
    ]
