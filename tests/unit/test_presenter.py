import pytest

from matching_engine.domain.events import (
    BookEntry,
    BookSnapshot,
    Event,
    OrderAccepted,
    OrderCancelled,
    Trade,
)
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


def cancelled(order_id: int = 1, price: int | None = 1050, remaining: int = 7) -> OrderCancelled:
    return OrderCancelled(
        order_id=OrderId(order_id),
        side=Side.SELL,
        price=None if price is None else Ticks(price),
        remaining=remaining,
    )


def book(bids: list[tuple[int, int]], asks: list[tuple[int, int]]) -> BookSnapshot:
    return BookSnapshot(
        bids=tuple(BookEntry(quantity=quantity, price=Ticks(price)) for quantity, price in bids),
        asks=tuple(BookEntry(quantity=quantity, price=Ticks(price)) for quantity, price in asks),
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


@pytest.mark.parametrize(
    "price",
    [Ticks(1000), None],
    ids=["ordem no livro", "pegged parked, sem preço"],
)
def test_a_cancellation_is_always_the_same_fixed_line(price: Ticks | None) -> None:
    """O enunciado fixa a linha inteira; id, lado e remanescente ficam só no evento."""
    event = OrderCancelled(order_id=OrderId(7), side=Side.BUY, price=price, remaining=50)

    assert format_events([event]) == ["Order cancelled"]


def test_an_empty_book_prints_only_the_header_and_the_separator() -> None:
    assert format_events([book([], [])]) == [
        "Ordens de Compra | Ordens de Venda",
        "-----------------|----------------",
    ]


def test_the_book_of_requirement_4() -> None:
    """200 @ 10 e 100 @ 9.99 na compra, 100 @ 10.5 na venda, como o enunciado exibe."""
    snapshot = book(bids=[(200, 1000), (100, 999)], asks=[(100, 1050)])

    assert format_events([snapshot]) == [
        "Ordens de Compra | Ordens de Venda",
        "-----------------|----------------",
        "200 @ 10         | 100 @ 10.5",
        "100 @ 9.99       |",
    ]


def test_a_book_with_only_bids() -> None:
    assert format_events([book(bids=[(200, 1000)], asks=[])]) == [
        "Ordens de Compra | Ordens de Venda",
        "-----------------|----------------",
        "200 @ 10         |",
    ]


def test_a_book_with_only_asks() -> None:
    assert format_events([book(bids=[], asks=[(100, 1050)])]) == [
        "Ordens de Compra | Ordens de Venda",
        "-----------------|----------------",
        "                 | 100 @ 10.5",
    ]


def test_the_shorter_side_is_padded_until_the_table_is_rectangular() -> None:
    snapshot = book(bids=[(200, 1000), (100, 999), (50, 998)], asks=[(100, 1050)])

    assert format_events([snapshot]) == [
        "Ordens de Compra | Ordens de Venda",
        "-----------------|----------------",
        "200 @ 10         | 100 @ 10.5",
        "100 @ 9.99       |",
        "50 @ 9.98        |",
    ]


def test_the_column_widens_to_fit_the_longest_line() -> None:
    """Cabeçalho é o piso da largura; uma linha maior que ele empurra a coluna."""
    snapshot = book(bids=[(1000000, 123456)], asks=[(2000000, 654321)])

    assert format_events([snapshot]) == [
        "Ordens de Compra  | Ordens de Venda",
        "------------------|------------------",
        "1000000 @ 1234.56 | 2000000 @ 6543.21",
    ]


@pytest.mark.parametrize(
    "snapshot",
    [
        book([], []),
        book(bids=[(200, 1000), (100, 999)], asks=[(100, 1050)]),
        book(bids=[(200, 1000)], asks=[]),
        book(bids=[], asks=[(100, 1050)]),
        book(bids=[(1000000, 123456)], asks=[(1, 1)]),
    ],
)
def test_no_line_ends_in_whitespace(snapshot: BookSnapshot) -> None:
    """Espaço invisível quebra teste golden sem que a diferença apareça na tela."""
    for line in format_events([snapshot]):
        assert line == line.rstrip()


def test_the_book_does_not_absorb_the_events_around_it() -> None:
    events: list[Event] = [trade(2000, 100), book(bids=[(50, 1000)], asks=[])]

    assert format_events(events) == [
        "Trade, price: 20, qty: 100",
        "Ordens de Compra | Ordens de Venda",
        "-----------------|----------------",
        "50 @ 10          |",
    ]


def test_every_event_type_closes_the_pending_run_of_trades() -> None:
    """Trades separados por qualquer outro evento não se fundem, e a cronologia se mantém.

    Os três trades têm o mesmo preço de propósito: fundissem-se, o resultado seria uma
    linha só de 60, ou três linhas fora da ordem em que os eventos chegaram. É a
    propriedade que o guard único antes do ``match`` garante para todo evento — inclusive
    os que ainda não existem.
    """
    events: list[Event] = [
        trade(2000, 10),
        accepted(999, 50, side=Side.BUY, order_id=4),
        trade(2000, 20),
        cancelled(order_id=5),
        trade(2000, 30),
        book(bids=[(50, 1000)], asks=[]),
    ]

    assert format_events(events) == [
        "Trade, price: 20, qty: 10",
        "Order created: buy 50 @ 9.99 4",
        "Trade, price: 20, qty: 20",
        "Order cancelled",
        "Trade, price: 20, qty: 30",
        "Ordens de Compra | Ordens de Venda",
        "-----------------|----------------",
        "50 @ 10          |",
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
