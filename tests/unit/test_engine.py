from collections.abc import Sequence

import pytest

from matching_engine.domain.engine import MatchingEngine
from matching_engine.domain.events import Event, OrderAccepted, Trade
from matching_engine.domain.order import Order
from matching_engine.domain.price import Ticks
from matching_engine.domain.side import Side

SIDES = list(Side)


def trades_of(events: Sequence[Event]) -> list[Trade]:
    return [event for event in events if isinstance(event, Trade)]


def acceptances_of(events: Sequence[Event]) -> list[OrderAccepted]:
    return [event for event in events if isinstance(event, OrderAccepted)]


def traded_by_price(events: Sequence[Event]) -> dict[Ticks, int]:
    """Agrega como o enunciado agrega na saída; o domínio emite um Trade por par."""
    totals: dict[Ticks, int] = {}
    for trade in trades_of(events):
        totals[trade.price] = totals.get(trade.price, 0) + trade.quantity
    return totals


def resting(engine: MatchingEngine, side: Side, price: Ticks, quantity: int) -> Order:
    """Insere uma passiva e devolve a ordem viva que ficou no livro."""
    events = engine.submit_limit(side, price, quantity)
    [accepted] = acceptances_of(events)
    order = engine.book.get(accepted.order_id)
    assert order is not None
    return order


def top_of(engine: MatchingEngine, side: Side) -> Ticks | None:
    return engine.book.best_bid if side is Side.BUY else engine.book.best_ask


@pytest.mark.parametrize("side", SIDES)
def test_market_on_an_empty_book_trades_nothing_and_leaves_nothing(side: Side) -> None:
    engine = MatchingEngine()

    events = engine.submit_market(side, 100)

    assert events == []
    assert len(engine.book) == 0
    assert engine.book.best_bid is None
    assert engine.book.best_ask is None


@pytest.mark.parametrize("side", SIDES)
def test_limit_on_an_empty_book_rests_and_is_accepted(side: Side) -> None:
    engine = MatchingEngine()

    events = engine.submit_limit(side, Ticks(1000), 100)

    [accepted] = acceptances_of(events)
    assert events == [accepted]
    assert accepted.side is side
    assert accepted.price == 1000
    assert accepted.quantity == 100
    assert accepted.order_id in engine.book
    assert top_of(engine, side) == 1000
    assert top_of(engine, side.opposite) is None


def test_limit_that_does_not_cross_rests_without_trading() -> None:
    engine = MatchingEngine()
    resting(engine, Side.BUY, Ticks(1000), 100)

    events = engine.submit_limit(Side.SELL, Ticks(1100), 100)

    assert trades_of(events) == []
    assert len(acceptances_of(events)) == 1
    assert engine.book.best_bid == 1000
    assert engine.book.best_ask == 1100
    assert len(engine.book) == 2


def test_market_consumes_exactly_one_order() -> None:
    engine = MatchingEngine()
    maker = resting(engine, Side.SELL, Ticks(2000), 100)

    events = engine.submit_market(Side.BUY, 100)

    assert events == [
        Trade(price=Ticks(2000), quantity=100, maker_order_id=maker.order_id, taker_side=Side.BUY)
    ]
    assert len(engine.book) == 0
    assert engine.book.best_ask is None


def test_market_consumes_part_of_one_order() -> None:
    engine = MatchingEngine()
    maker = resting(engine, Side.SELL, Ticks(2000), 100)

    events = engine.submit_market(Side.BUY, 40)

    [trade] = trades_of(events)
    assert trade.quantity == 40
    assert maker.remaining == 60
    assert maker.order_id in engine.book
    level = engine.book.side(Side.SELL).level_at(Ticks(2000))
    assert level is not None
    assert level.total_quantity == 60
    assert engine.book.best_ask == 2000


def test_market_walks_two_orders_of_the_same_level_in_fifo() -> None:
    engine = MatchingEngine()
    first = resting(engine, Side.SELL, Ticks(2000), 100)
    second = resting(engine, Side.SELL, Ticks(2000), 200)

    events = engine.submit_market(Side.BUY, 150)

    assert trades_of(events) == [
        Trade(price=Ticks(2000), quantity=100, maker_order_id=first.order_id, taker_side=Side.BUY),
        Trade(price=Ticks(2000), quantity=50, maker_order_id=second.order_id, taker_side=Side.BUY),
    ]
    assert first.order_id not in engine.book
    assert second.remaining == 150
    level = engine.book.side(Side.SELL).level_at(Ticks(2000))
    assert level is not None
    assert level.total_quantity == 150


def test_market_walks_two_price_levels_from_the_best_one() -> None:
    engine = MatchingEngine()
    far = resting(engine, Side.SELL, Ticks(2100), 100)
    near = resting(engine, Side.SELL, Ticks(2000), 100)

    events = engine.submit_market(Side.BUY, 150)

    assert trades_of(events) == [
        Trade(price=Ticks(2000), quantity=100, maker_order_id=near.order_id, taker_side=Side.BUY),
        Trade(price=Ticks(2100), quantity=50, maker_order_id=far.order_id, taker_side=Side.BUY),
    ]
    assert near.order_id not in engine.book
    assert far.remaining == 50
    assert engine.book.best_ask == 2100


def test_market_without_enough_liquidity_drops_the_remainder() -> None:
    """IOC: os 50 que não acharam contraparte somem, não repousam no livro."""
    engine = MatchingEngine()
    resting(engine, Side.SELL, Ticks(2000), 150)

    events = engine.submit_market(Side.BUY, 200)

    assert traded_by_price(events) == {Ticks(2000): 150}
    assert acceptances_of(events) == []
    assert len(engine.book) == 0
    assert engine.book.best_bid is None
    assert engine.book.best_ask is None


def test_marketable_limit_that_crosses_partially_rests_the_remainder() -> None:
    engine = MatchingEngine()
    maker = resting(engine, Side.SELL, Ticks(2000), 100)

    events = engine.submit_limit(Side.BUY, Ticks(2000), 150)

    [trade] = trades_of(events)
    assert trade.price == 2000
    assert trade.quantity == 100
    assert trade.maker_order_id == maker.order_id
    [accepted] = acceptances_of(events)
    assert accepted.side is Side.BUY
    assert accepted.price == 2000
    assert accepted.quantity == 50
    assert engine.book.best_bid == 2000
    assert engine.book.best_ask is None
    assert len(engine.book) == 1


def test_marketable_limit_that_is_fully_filled_is_never_accepted() -> None:
    engine = MatchingEngine()
    maker = resting(engine, Side.SELL, Ticks(2000), 200)

    events = engine.submit_limit(Side.BUY, Ticks(2000), 150)

    assert acceptances_of(events) == []
    assert events == [
        Trade(price=Ticks(2000), quantity=150, maker_order_id=maker.order_id, taker_side=Side.BUY)
    ]
    assert maker.remaining == 50
    assert engine.book.best_bid is None
    assert len(engine.book) == 1


def test_marketable_limit_stops_at_its_own_price_limit() -> None:
    engine = MatchingEngine()
    resting(engine, Side.SELL, Ticks(2000), 100)
    resting(engine, Side.SELL, Ticks(2100), 100)

    events = engine.submit_limit(Side.BUY, Ticks(2000), 250)

    assert traded_by_price(events) == {Ticks(2000): 100}
    [accepted] = acceptances_of(events)
    assert accepted.quantity == 150
    assert engine.book.best_bid == 2000
    assert engine.book.best_ask == 2100


@pytest.mark.parametrize(
    ("maker_side", "maker_price", "taker_price"),
    [
        (Side.BUY, Ticks(1000), Ticks(900)),
        (Side.SELL, Ticks(1000), Ticks(1100)),
    ],
)
def test_the_trade_happens_at_the_maker_price(
    maker_side: Side, maker_price: Ticks, taker_price: Ticks
) -> None:
    """Bid 10 no livro e chega limit sell 9: o trade sai a 10, com price improvement."""
    engine = MatchingEngine()
    maker = resting(engine, maker_side, maker_price, 100)

    events = engine.submit_limit(maker_side.opposite, taker_price, 100)

    assert events == [
        Trade(
            price=maker_price,
            quantity=100,
            maker_order_id=maker.order_id,
            taker_side=maker_side.opposite,
        )
    ]
    assert len(engine.book) == 0


def test_the_book_is_never_left_crossed() -> None:
    engine = MatchingEngine()
    script: list[tuple[Side, Ticks | None, int]] = [
        (Side.BUY, Ticks(1000), 100),
        (Side.SELL, Ticks(1100), 100),
        (Side.BUY, Ticks(1200), 250),  # agressiva, varre a offer e repousa acima do bid
        (Side.SELL, Ticks(900), 400),  # agressiva, varre os dois níveis de bid
        (Side.BUY, None, 100),
        (Side.SELL, Ticks(950), 50),
        (Side.BUY, Ticks(950), 300),
        (Side.SELL, None, 500),
    ]

    for side, price, quantity in script:
        if price is None:
            engine.submit_market(side, quantity)
        else:
            engine.submit_limit(side, price, quantity)

        best_bid, best_ask = engine.book.best_bid, engine.book.best_ask
        assert best_bid is None or best_ask is None or best_bid < best_ask


def test_the_examples_from_the_statement_as_one_sequence() -> None:
    """Agregação por preço, já que o domínio emite um Trade por par maker/taker."""
    engine = MatchingEngine()
    engine.submit_limit(Side.BUY, Ticks(1000), 100)
    engine.submit_limit(Side.SELL, Ticks(2000), 100)
    engine.submit_limit(Side.SELL, Ticks(2000), 200)

    first = engine.submit_market(Side.BUY, 150)
    assert traded_by_price(first) == {Ticks(2000): 150}
    assert acceptances_of(first) == []

    # só restam 150 na offer: executa o que há e descarta os outros 50
    second = engine.submit_market(Side.BUY, 200)
    assert traded_by_price(second) == {Ticks(2000): 150}
    assert acceptances_of(second) == []
    assert engine.book.best_ask is None

    third = engine.submit_market(Side.SELL, 200)
    assert traded_by_price(third) == {Ticks(1000): 100}
    assert acceptances_of(third) == []
    assert len(engine.book) == 0
    assert engine.book.best_bid is None


def test_a_fully_filled_order_leaves_the_global_index() -> None:
    engine = MatchingEngine()
    first = resting(engine, Side.SELL, Ticks(2000), 100)
    second = resting(engine, Side.SELL, Ticks(2000), 100)

    engine.submit_market(Side.BUY, 150)

    assert first.order_id not in engine.book
    assert engine.book.get(first.order_id) is None
    assert second.order_id in engine.book
    assert len(engine.book) == 1


def test_an_emptied_level_leaves_the_price_index() -> None:
    engine = MatchingEngine()
    resting(engine, Side.SELL, Ticks(2000), 100)
    resting(engine, Side.SELL, Ticks(2100), 100)

    engine.submit_market(Side.BUY, 100)

    asks = engine.book.side(Side.SELL)
    assert asks.level_at(Ticks(2000)) is None
    assert len(asks) == 1
    assert engine.book.best_ask == 2100

    engine.submit_market(Side.BUY, 100)

    assert asks.level_at(Ticks(2100)) is None
    assert asks.is_empty
    assert engine.book.best_ask is None
