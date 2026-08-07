from collections.abc import Callable, Sequence

import pytest

from matching_engine.domain.engine import MatchingEngine, OrderNotFoundError
from matching_engine.domain.events import (
    BookEntry,
    BookSnapshot,
    Event,
    OrderAccepted,
    OrderAmended,
    OrderCancelled,
    OrderPegged,
    Trade,
)
from matching_engine.domain.order import InvalidOrderError, Order, OrderId
from matching_engine.domain.price import Ticks
from matching_engine.domain.price_level import PriceLevel
from matching_engine.domain.side import PegReference, Side

SIDES = list(Side)


def trades_of(events: Sequence[Event]) -> list[Trade]:
    return [event for event in events if isinstance(event, Trade)]


def acceptances_of(events: Sequence[Event]) -> list[OrderAccepted]:
    return [event for event in events if isinstance(event, OrderAccepted)]


def amendments_of(events: Sequence[Event]) -> list[OrderAmended]:
    return [event for event in events if isinstance(event, OrderAmended)]


def pegs_of(events: Sequence[Event]) -> list[OrderPegged]:
    return [event for event in events if isinstance(event, OrderPegged)]


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


def pegged(engine: MatchingEngine, side: Side, quantity: int) -> Order:
    """Envia uma pegged homolateral — a única admitida — e devolve a ordem viva."""
    reference = PegReference.BID if side is Side.BUY else PegReference.OFFER
    [event] = pegs_of(engine.submit_pegged(reference, side, quantity))
    order = engine.book.get(event.order_id)
    assert order is not None
    return order


def improved(side: Side, price: Ticks, by: int = 10) -> Ticks:
    """Preço um passo melhor para este lado: acima no bid, abaixo na offer."""
    return Ticks(price + by) if side is Side.BUY else Ticks(price - by)


def top_of(engine: MatchingEngine, side: Side) -> Ticks | None:
    return engine.book.best_bid if side is Side.BUY else engine.book.best_ask


def assert_no_pegged_only_level(engine: MatchingEngine) -> None:
    """Item 7 dos invariantes na forma em que ele se apresenta em repouso — ver o teste."""
    for side in SIDES:
        book_side = engine.book.side(side)
        only_pegged = [level for level in book_side if level.non_pegged_count == 0]
        assert len(only_pegged) <= 1, f"lado {side.name} com mais de um nível só de pegged"
        if only_pegged:
            assert only_pegged[0] is book_side.best_level
        assert only_pegged == []


def level_of(engine: MatchingEngine, side: Side, price: Ticks) -> PriceLevel:
    """O nível daquele preço, que o teste espera existir."""
    level = engine.book.side(side).level_at(price)
    assert level is not None
    return level


def snapshot_of(engine: MatchingEngine) -> BookSnapshot:
    [event] = engine.snapshot()
    assert isinstance(event, BookSnapshot)
    return event


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


@pytest.mark.parametrize("side", SIDES)
def test_cancel_takes_the_order_out_of_the_whole_book(side: Side) -> None:
    engine = MatchingEngine()
    order = resting(engine, side, Ticks(1000), 100)

    events = engine.cancel(order.order_id)

    assert events == [
        OrderCancelled(order_id=order.order_id, side=side, price=Ticks(1000), remaining=100)
    ]
    assert order.order_id not in engine.book
    assert engine.book.side(side).level_at(Ticks(1000)) is None
    assert engine.book.side(side).is_empty
    assert top_of(engine, side) is None
    assert len(engine.book) == 0


def test_cancel_keeps_a_level_that_still_has_orders_and_its_queue() -> None:
    engine = MatchingEngine()
    first = resting(engine, Side.SELL, Ticks(2000), 100)
    second = resting(engine, Side.SELL, Ticks(2000), 200)
    third = resting(engine, Side.SELL, Ticks(2000), 300)

    engine.cancel(first.order_id)

    level = engine.book.side(Side.SELL).level_at(Ticks(2000))
    assert level is not None
    assert level.head is second
    assert list(level) == [second, third]
    assert level.total_quantity == 500
    assert len(engine.book) == 2
    assert engine.book.best_ask == 2000


@pytest.mark.parametrize(
    ("side", "top", "next_price"),
    [(Side.BUY, Ticks(1000), Ticks(990)), (Side.SELL, Ticks(1000), Ticks(1010))],
)
def test_cancelling_the_top_promotes_the_next_price(
    side: Side, top: Ticks, next_price: Ticks
) -> None:
    engine = MatchingEngine()
    best = resting(engine, side, top, 100)
    resting(engine, side, next_price, 100)

    engine.cancel(best.order_id)

    assert top_of(engine, side) == next_price
    assert len(engine.book.side(side)) == 1


def test_cancel_reports_the_remaining_quantity_not_the_original() -> None:
    engine = MatchingEngine()
    maker = resting(engine, Side.SELL, Ticks(2000), 100)
    engine.submit_market(Side.BUY, 40)

    events = engine.cancel(maker.order_id)

    assert maker.quantity == 100
    assert events == [
        OrderCancelled(order_id=maker.order_id, side=Side.SELL, price=Ticks(2000), remaining=60)
    ]


def test_cancelling_an_unknown_id_is_rejected() -> None:
    engine = MatchingEngine()

    with pytest.raises(OrderNotFoundError, match="não está no livro"):
        engine.cancel(OrderId(999))


def test_cancelling_twice_is_rejected() -> None:
    engine = MatchingEngine()
    order = resting(engine, Side.BUY, Ticks(1000), 100)
    engine.cancel(order.order_id)

    with pytest.raises(OrderNotFoundError):
        engine.cancel(order.order_id)


def test_cancelling_a_fully_executed_order_is_rejected() -> None:
    """Limitação conhecida: executada e inexistente dão a mesma ausência no índice."""
    engine = MatchingEngine()
    maker = resting(engine, Side.SELL, Ticks(2000), 100)
    engine.submit_market(Side.BUY, 100)

    with pytest.raises(OrderNotFoundError):
        engine.cancel(maker.order_id)


def test_order_not_found_error_is_a_value_error() -> None:
    """Errar o número da ordem é entrada inválida, não inconsistência interna da engine."""
    assert issubclass(OrderNotFoundError, ValueError)
    assert not issubclass(OrderNotFoundError, RuntimeError)


def test_cancelling_everything_returns_the_book_to_the_initial_state() -> None:
    engine = MatchingEngine()
    orders = [
        resting(engine, Side.BUY, Ticks(1000), 100),
        resting(engine, Side.BUY, Ticks(990), 100),
        resting(engine, Side.SELL, Ticks(1010), 100),
        resting(engine, Side.SELL, Ticks(1020), 100),
    ]

    for order in orders:
        engine.cancel(order.order_id)

    assert len(engine.book) == 0
    assert engine.book.best_bid is None
    assert engine.book.best_ask is None
    assert engine.book.side(Side.BUY).is_empty
    assert engine.book.side(Side.SELL).is_empty


def test_a_cancelled_order_no_longer_matches() -> None:
    """A liquidez saiu do livro: a agressiva que a teria atingido repousa em vez de executar."""
    engine = MatchingEngine()
    maker = resting(engine, Side.SELL, Ticks(2000), 100)
    engine.cancel(maker.order_id)

    events = engine.submit_limit(Side.BUY, Ticks(2000), 100)

    assert trades_of(events) == []
    assert len(acceptances_of(events)) == 1
    assert engine.book.best_bid == 2000


def test_reducing_the_quantity_keeps_the_place_in_the_queue() -> None:
    """A redução não prejudica quem está atrás, então não custa prioridade — ADR 0005."""
    engine = MatchingEngine()
    first = resting(engine, Side.BUY, Ticks(1000), 100)
    second = resting(engine, Side.BUY, Ticks(1000), 200)
    third = resting(engine, Side.BUY, Ticks(1000), 300)
    sequence_id = first.sequence_id

    events = engine.amend(first.order_id, None, 40)

    level = level_of(engine, Side.BUY, Ticks(1000))
    assert level.head is first
    assert list(level) == [first, second, third]
    assert first.sequence_id == sequence_id
    assert events == [
        OrderAmended(
            order_id=first.order_id,
            side=Side.BUY,
            price=Ticks(1000),
            quantity=40,
            priority_renewed=False,
        )
    ]


def test_reducing_the_quantity_lowers_the_level_total_by_the_difference() -> None:
    engine = MatchingEngine()
    first = resting(engine, Side.BUY, Ticks(1000), 100)
    resting(engine, Side.BUY, Ticks(1000), 200)

    engine.amend(first.order_id, None, 40)

    assert level_of(engine, Side.BUY, Ticks(1000)).total_quantity == 240
    assert first.quantity == 40
    assert first.remaining == 40


def test_reducing_a_partially_filled_order_above_what_it_executed() -> None:
    """Ordem de 100 com 40 executados, alterada para 80: redução perante a quantidade."""
    engine = MatchingEngine()
    maker = resting(engine, Side.SELL, Ticks(2000), 100)
    engine.submit_market(Side.BUY, 40)

    events = engine.amend(maker.order_id, None, 80)

    assert maker.quantity == 80
    assert maker.remaining == 40  # o saldo é o que resta do pedido novo: 80 - 40
    assert level_of(engine, Side.SELL, Ticks(2000)).total_quantity == 40
    [amended] = amendments_of(events)
    assert amended.quantity == 40
    assert not amended.priority_renewed


@pytest.mark.parametrize("new_quantity", [40, 30, 1])
def test_reducing_to_at_most_what_was_executed_is_rejected(new_quantity: int) -> None:
    """Os 40 negociados não voltam: uma ordem de 100 com 40 executados não vira de 30."""
    engine = MatchingEngine()
    maker = resting(engine, Side.SELL, Ticks(2000), 100)
    engine.submit_market(Side.BUY, 40)

    with pytest.raises(ValueError, match="já executou 40"):
        engine.amend(maker.order_id, None, new_quantity)

    assert maker.quantity == 100  # a alteração recusada não deixa rastro
    assert maker.remaining == 60
    assert level_of(engine, Side.SELL, Ticks(2000)).total_quantity == 60


@pytest.mark.parametrize("new_quantity", [0, -5])
def test_reducing_to_zero_or_less_is_rejected(new_quantity: int) -> None:
    engine = MatchingEngine()
    order = resting(engine, Side.BUY, Ticks(1000), 100)

    with pytest.raises(ValueError, match="não pode ser reduzida"):
        engine.amend(order.order_id, None, new_quantity)

    assert order.quantity == 100
    assert order.remaining == 100
    assert level_of(engine, Side.BUY, Ticks(1000)).total_quantity == 100


def test_increasing_the_quantity_sends_the_order_to_the_back_of_the_queue() -> None:
    """Crescer é pedir mais fila do que a que já se tinha, e isso se paga com tempo."""
    engine = MatchingEngine()
    first = resting(engine, Side.BUY, Ticks(1000), 100)
    second = resting(engine, Side.BUY, Ticks(1000), 200)

    events = engine.amend(first.order_id, None, 150)

    level = level_of(engine, Side.BUY, Ticks(1000))
    assert level.head is second
    assert list(level) == [second, first]
    assert level.total_quantity == 350
    assert first.quantity == 150
    assert first.remaining == 150
    [amended] = amendments_of(events)
    assert amended.quantity == 150
    assert amended.priority_renewed


def test_changing_the_price_to_an_existing_level_enters_at_the_back_of_it() -> None:
    engine = MatchingEngine()
    moved = resting(engine, Side.BUY, Ticks(1000), 200)
    settled = resting(engine, Side.BUY, Ticks(999), 100)

    engine.amend(moved.order_id, Ticks(999), None)

    level = level_of(engine, Side.BUY, Ticks(999))
    assert list(level) == [settled, moved]
    assert level.total_quantity == 300
    assert engine.book.side(Side.BUY).level_at(Ticks(1000)) is None
    assert engine.book.best_bid == 999
    assert len(engine.book) == 2


def test_changing_the_price_to_a_new_level_creates_it_and_drops_the_emptied_one() -> None:
    engine = MatchingEngine()
    order = resting(engine, Side.BUY, Ticks(1000), 200)

    engine.amend(order.order_id, Ticks(998), None)

    bids = engine.book.side(Side.BUY)
    assert bids.level_at(Ticks(1000)) is None
    assert list(level_of(engine, Side.BUY, Ticks(998))) == [order]
    assert len(bids) == 1
    assert order.price == 998
    assert engine.book.best_bid == 998


def test_the_order_id_is_preserved_and_the_sequence_id_is_new() -> None:
    """Identidade é do cliente, prioridade é da fila: só a segunda muda no amend."""
    engine = MatchingEngine()
    order = resting(engine, Side.BUY, Ticks(1000), 100)
    order_id, sequence_id = order.order_id, order.sequence_id

    [amended] = amendments_of(engine.amend(order_id, Ticks(990), None))

    assert amended.order_id == order_id
    assert engine.book.get(order_id) is order
    assert order.sequence_id > sequence_id
    assert len(engine.book) == 1


def test_the_example_of_requirement_4() -> None:
    """Bids 200 @ 10 e 100 @ 9.99; alterar a de 200 para 9.98 a leva para baixo da de 9.99."""
    engine = MatchingEngine()
    moved = resting(engine, Side.BUY, Ticks(1000), 200)
    resting(engine, Side.BUY, Ticks(999), 100)

    engine.amend(moved.order_id, Ticks(998), None)

    assert snapshot_of(engine).bids == (
        BookEntry(quantity=100, price=Ticks(999)),
        BookEntry(quantity=200, price=Ticks(998)),
    )


def test_an_amend_that_makes_the_order_marketable_executes_and_rests_the_remainder() -> None:
    """Preço agressivo é preço agressivo, tenha vindo de submit ou de amend — ADR 0001."""
    engine = MatchingEngine()
    maker = resting(engine, Side.SELL, Ticks(2000), 100)
    taker = resting(engine, Side.BUY, Ticks(1000), 150)

    events = engine.amend(taker.order_id, Ticks(2000), None)

    assert trades_of(events) == [
        Trade(price=Ticks(2000), quantity=100, maker_order_id=maker.order_id, taker_side=Side.BUY)
    ]
    [amended] = amendments_of(events)
    assert amended.price == 2000
    assert amended.quantity == 50
    assert amended.priority_renewed
    assert taker.quantity == 150
    assert taker.remaining == 50
    assert engine.book.best_bid == 2000
    assert engine.book.best_ask is None
    assert len(engine.book) == 1


def test_an_amend_that_fills_the_order_completely_emits_only_trades() -> None:
    engine = MatchingEngine()
    maker = resting(engine, Side.SELL, Ticks(2000), 200)
    taker = resting(engine, Side.BUY, Ticks(1000), 150)

    events = engine.amend(taker.order_id, Ticks(2000), None)

    assert events == [
        Trade(price=Ticks(2000), quantity=150, maker_order_id=maker.order_id, taker_side=Side.BUY)
    ]
    assert taker.is_filled
    assert taker.order_id not in engine.book
    assert engine.book.get(taker.order_id) is None
    assert engine.book.best_bid is None
    assert len(engine.book) == 1


def test_an_amend_that_changes_price_and_quantity_at_once_renews_once() -> None:
    engine = MatchingEngine()
    first = resting(engine, Side.BUY, Ticks(1000), 100)
    second = resting(engine, Side.BUY, Ticks(999), 200)

    events = engine.amend(first.order_id, Ticks(999), 60)

    level = level_of(engine, Side.BUY, Ticks(999))
    assert list(level) == [second, first]  # reduzir junto com mudar de preço não salva a fila
    assert level.total_quantity == 260
    assert first.quantity == 60
    [amended] = amendments_of(events)
    assert amended.quantity == 60
    assert amended.priority_renewed


def test_an_amend_that_asks_for_nothing_is_rejected() -> None:
    engine = MatchingEngine()
    order = resting(engine, Side.BUY, Ticks(1000), 100)

    with pytest.raises(ValueError, match="nada a alterar"):
        engine.amend(order.order_id, None, None)

    assert order.remaining == 100


def test_amending_an_unknown_id_is_rejected() -> None:
    engine = MatchingEngine()

    with pytest.raises(OrderNotFoundError, match="não está no livro"):
        engine.amend(OrderId(999), Ticks(1000), None)


def test_amending_an_order_that_is_no_longer_in_the_book_is_rejected() -> None:
    engine = MatchingEngine()
    maker = resting(engine, Side.SELL, Ticks(2000), 100)
    engine.submit_market(Side.BUY, 100)

    with pytest.raises(OrderNotFoundError):
        engine.amend(maker.order_id, Ticks(2100), None)


def test_amending_to_the_same_price_does_not_renew_the_priority() -> None:
    """Alteração que não altera nada é resposta, não erro: a ordem já está onde se pediu."""
    engine = MatchingEngine()
    first = resting(engine, Side.BUY, Ticks(1000), 100)
    second = resting(engine, Side.BUY, Ticks(1000), 200)
    sequence_id = first.sequence_id

    events = engine.amend(first.order_id, Ticks(1000), None)

    [amended] = amendments_of(events)
    assert not amended.priority_renewed
    assert amended.quantity == 100
    assert first.sequence_id == sequence_id
    assert list(level_of(engine, Side.BUY, Ticks(1000))) == [first, second]
    assert level_of(engine, Side.BUY, Ticks(1000)).total_quantity == 300


def test_the_book_is_never_left_crossed_after_an_amend() -> None:
    engine = MatchingEngine()
    orders = [
        resting(engine, Side.BUY, Ticks(1000), 100),
        resting(engine, Side.BUY, Ticks(990), 200),
        resting(engine, Side.SELL, Ticks(1100), 150),
        resting(engine, Side.SELL, Ticks(1200), 300),
    ]
    script: list[tuple[int, Ticks | None, int | None]] = [
        (0, Ticks(1050), None),  # sobe o bid sem alcançar a offer
        (1, None, 400),  # aumenta a quantidade, no mesmo preço
        (2, Ticks(1050), None),  # a offer desce até o bid: executa e repousa o resto
        (3, Ticks(950), None),  # a offer atravessa o bid inteiro e não sobra nada
        (1, Ticks(1100), None),  # o bid sobe e varre o que restou da offer
    ]

    for index, price, quantity in script:
        engine.amend(orders[index].order_id, price, quantity)

        best_bid, best_ask = engine.book.best_bid, engine.book.best_ask
        assert best_bid is None or best_ask is None or best_bid < best_ask

    assert engine.book.best_bid == 1100
    assert engine.book.best_ask is None


def test_a_pegged_order_enters_behind_the_orders_already_at_the_reference_price() -> None:
    """A pegged que **chega** é a ordem mais nova do livro; a que é reprecificada é que
    conserva o lugar. No exemplo do enunciado ela entra atrás dos 200 @ 10."""
    engine = MatchingEngine()
    first = resting(engine, Side.BUY, Ticks(1000), 200)
    second = resting(engine, Side.BUY, Ticks(1000), 100)

    peg = pegged(engine, Side.BUY, 150)

    level = level_of(engine, Side.BUY, Ticks(1000))
    assert list(level) == [first, second, peg]
    assert level.total_quantity == 450
    assert level.non_pegged_count == 2
    assert peg.price == 1000
    assert engine.book.best_bid == 1000


def test_the_example_of_requirement_5() -> None:
    """A limit nova melhora o topo, a pegged sobe com ela e fica **acima** dela.

    A pegged já estava no livro quando a limit chegou, e reprecificar não é reenviar: o
    ``sequence_id`` é preservado, então ela continua na frente de quem chegou depois.
    """
    engine = MatchingEngine()
    limit = resting(engine, Side.BUY, Ticks(1000), 200)
    peg = pegged(engine, Side.BUY, 150)
    assert list(level_of(engine, Side.BUY, Ticks(1000))) == [limit, peg]
    sequence_id = peg.sequence_id

    events = engine.submit_limit(Side.BUY, Ticks(1010), 300)

    assert pegs_of(events) == [
        OrderPegged(
            order_id=peg.order_id,
            side=Side.BUY,
            reference=PegReference.BID,
            price=Ticks(1010),
            quantity=150,
        )
    ]
    assert peg.sequence_id == sequence_id
    assert snapshot_of(engine).bids == (
        BookEntry(quantity=150, price=Ticks(1010)),
        BookEntry(quantity=300, price=Ticks(1010)),
        BookEntry(quantity=200, price=Ticks(1000)),
    )


def test_a_pegged_order_already_at_the_reference_price_is_not_touched() -> None:
    """Retirar e reinserir devolveria o mesmo livro por acidente, ao custo de K remoções."""
    engine = MatchingEngine()
    first = resting(engine, Side.BUY, Ticks(1000), 200)
    peg = pegged(engine, Side.BUY, 150)
    sequence_id = peg.sequence_id

    events = engine.submit_limit(Side.BUY, Ticks(1000), 100)

    assert pegs_of(events) == []
    [accepted] = acceptances_of(events)
    assert list(level_of(engine, Side.BUY, Ticks(1000))) == [
        first,
        peg,
        engine.book.get(accepted.order_id),
    ]
    assert peg.sequence_id == sequence_id


def test_cancelling_the_top_lowers_the_pegged_into_the_level_below() -> None:
    """Aqui há intercalação de verdade: o nível de destino já existia e guarda uma ordem
    **mais nova** que a pegged, que por isso entra no meio da fila e não no fim dela."""
    engine = MatchingEngine()
    oldest = resting(engine, Side.BUY, Ticks(1000), 200)
    top = resting(engine, Side.BUY, Ticks(1010), 300)
    peg = pegged(engine, Side.BUY, 150)
    newest = resting(engine, Side.BUY, Ticks(1000), 100)
    assert list(level_of(engine, Side.BUY, Ticks(1010))) == [top, peg]

    events = engine.cancel(top.order_id)

    [lowered] = pegs_of(events)
    assert lowered.order_id == peg.order_id
    assert lowered.price == 1000
    assert engine.book.side(Side.BUY).level_at(Ticks(1010)) is None
    level = level_of(engine, Side.BUY, Ticks(1000))
    assert list(level) == [oldest, peg, newest]
    assert level.total_quantity == 450
    assert engine.book.best_bid == 1000


def test_a_pegged_order_without_a_reference_is_parked() -> None:
    """Sem não-pegged do seu lado não há a que se ancorar; descartá-la perderia a intenção."""
    engine = MatchingEngine()

    events = engine.submit_pegged(PegReference.BID, Side.BUY, 150)

    assert events == [
        OrderPegged(
            order_id=OrderId(1),
            side=Side.BUY,
            reference=PegReference.BID,
            price=None,
            quantity=150,
        )
    ]
    order = engine.book.get(OrderId(1))
    assert order is not None
    assert order.is_parked
    assert engine.book.side(Side.BUY).is_empty
    assert engine.book.best_bid is None
    assert len(engine.book) == 1


def test_a_parked_pegged_order_comes_back_when_a_reference_appears() -> None:
    """E volta **à frente** da limit que lhe deu referência: ela chegou antes, e a sequência
    guardada é que decide. É o mesmo motivo pelo qual a pegged reprecificada sobe na fila."""
    engine = MatchingEngine()
    peg = pegged(engine, Side.BUY, 150)
    assert peg.is_parked

    events = engine.submit_limit(Side.BUY, Ticks(1000), 200)

    [accepted] = acceptances_of(events)
    [reactivated] = pegs_of(events)
    assert reactivated.order_id == peg.order_id
    assert reactivated.price == 1000
    assert reactivated.quantity == 150
    assert not peg.is_parked
    level = level_of(engine, Side.BUY, Ticks(1000))
    assert list(level) == [peg, engine.book.get(accepted.order_id)]
    assert level.total_quantity == 350
    assert len(engine.book) == 2


def test_a_parked_pegged_order_can_be_cancelled() -> None:
    """Ela está viva e é achada pelo id como qualquer outra — é para isso que ``park`` a
    mantém no índice global."""
    engine = MatchingEngine()
    peg = pegged(engine, Side.BUY, 150)

    events = engine.cancel(peg.order_id)

    assert events == [
        OrderCancelled(order_id=peg.order_id, side=Side.BUY, price=None, remaining=150)
    ]
    assert peg.order_id not in engine.book
    assert len(engine.book) == 0


@pytest.mark.parametrize("new_quantity", [None, 60], ids=["só preço", "preço e quantidade"])
def test_amending_the_price_of_a_pegged_order_is_rejected(new_quantity: int | None) -> None:
    """O preço de uma pegged é delegado à engine — é a definição do tipo de ordem.

    Aceito, o pedido seria desfeito pela reconciliação no mesmo comando: ``OrderAmended``
    com o preço pedido e ``OrderPegged`` desfazendo-o na mesma resposta, com a ordem
    pagando prioridade por uma mudança que nunca vigora.
    """
    engine = MatchingEngine()
    limit = resting(engine, Side.BUY, Ticks(1000), 200)
    peg = pegged(engine, Side.BUY, 150)
    sequence_id = peg.sequence_id

    with pytest.raises(ValueError, match="ditado pelo livro"):
        engine.amend(peg.order_id, Ticks(1010), new_quantity)

    assert peg.price == 1000  # a recusa não deixa rastro
    assert peg.sequence_id == sequence_id
    assert peg.remaining == 150
    assert list(level_of(engine, Side.BUY, Ticks(1000))) == [limit, peg]
    assert engine.book.best_bid == 1000


def test_amending_the_price_of_a_parked_pegged_order_is_rejected() -> None:
    """Parked ou no livro, a recusa é a mesma: o preço de uma pegged não é do cliente."""
    engine = MatchingEngine()
    peg = pegged(engine, Side.BUY, 150)
    assert peg.is_parked

    with pytest.raises(ValueError, match="ditado pelo livro"):
        engine.amend(peg.order_id, Ticks(1010), None)

    assert peg.is_parked
    assert peg.price is None
    assert len(engine.book) == 1


def test_amending_the_quantity_of_a_pegged_order_reduces_in_place() -> None:
    """Quantidade não é preço: a redução segue a política comum e mantém o lugar na fila."""
    engine = MatchingEngine()
    limit = resting(engine, Side.BUY, Ticks(1000), 200)
    peg = pegged(engine, Side.BUY, 150)
    behind = resting(engine, Side.BUY, Ticks(1000), 100)
    sequence_id = peg.sequence_id

    events = engine.amend(peg.order_id, None, 40)

    level = level_of(engine, Side.BUY, Ticks(1000))
    assert list(level) == [limit, peg, behind]
    assert peg.sequence_id == sequence_id
    assert peg.quantity == 40
    assert peg.remaining == 40
    assert level.total_quantity == 340
    [amended] = amendments_of(events)
    assert amended.quantity == 40
    assert not amended.priority_renewed
    assert pegs_of(events) == []  # a referência não se moveu, e a reconciliação não toca nela


def test_amending_the_quantity_of_a_parked_pegged_order_is_rejected() -> None:
    """Fora do livro não há nível: nem redução in place, nem preço a que uma renovação
    devolvesse a ordem. Cancelar é o único caminho, e tem teste próprio."""
    engine = MatchingEngine()
    peg = pegged(engine, Side.BUY, 150)

    with pytest.raises(ValueError, match="está parked, à espera de referência"):
        engine.amend(peg.order_id, None, 40)

    assert peg.is_parked
    assert peg.remaining == 150
    assert len(engine.book) == 1


def test_several_pegged_orders_share_the_price_and_keep_their_order_among_themselves() -> None:
    engine = MatchingEngine()
    resting(engine, Side.BUY, Ticks(1000), 200)
    first = pegged(engine, Side.BUY, 100)
    second = pegged(engine, Side.BUY, 50)

    events = engine.submit_limit(Side.BUY, Ticks(1010), 300)

    [accepted] = acceptances_of(events)
    lifted = pegs_of(events)
    assert [event.order_id for event in lifted] == [first.order_id, second.order_id]
    assert [event.price for event in lifted] == [Ticks(1010), Ticks(1010)]
    level = level_of(engine, Side.BUY, Ticks(1010))
    assert list(level) == [first, second, engine.book.get(accepted.order_id)]
    assert level.total_quantity == 450


def test_a_pegged_order_is_filled_by_an_aggressor_like_any_other() -> None:
    """Ela repousa num nível e é consumida pela fila, sem nada de especial no matching. O que
    a distingue vem depois: sumida a não-pegged que lhe dava referência, ela vai para parked
    em vez de ficar sozinha anunciando um preço que já não tem quem o sustente."""
    engine = MatchingEngine()
    limit = resting(engine, Side.BUY, Ticks(1000), 200)
    peg = pegged(engine, Side.BUY, 150)

    events = engine.submit_market(Side.SELL, 300)

    assert trades_of(events) == [
        Trade(
            price=Ticks(1000),
            quantity=200,
            maker_order_id=limit.order_id,
            taker_side=Side.SELL,
        ),
        Trade(
            price=Ticks(1000),
            quantity=100,
            maker_order_id=peg.order_id,
            taker_side=Side.SELL,
        ),
    ]
    [parked] = pegs_of(events)
    assert parked.order_id == peg.order_id
    assert parked.price is None
    assert parked.quantity == 50
    assert peg.is_parked
    assert peg.remaining == 50
    assert engine.book.side(Side.BUY).is_empty
    assert len(engine.book) == 1


def test_a_partially_filled_pegged_order_keeps_its_place_when_repriced() -> None:
    engine = MatchingEngine()
    resting(engine, Side.BUY, Ticks(1000), 200)
    peg = pegged(engine, Side.BUY, 150)
    behind = resting(engine, Side.BUY, Ticks(1000), 100)

    engine.submit_market(Side.SELL, 250)
    assert peg.remaining == 100
    assert list(level_of(engine, Side.BUY, Ticks(1000))) == [peg, behind]

    events = engine.submit_limit(Side.BUY, Ticks(1010), 300)

    [lifted] = pegs_of(events)
    assert lifted.quantity == 100  # o saldo, e não os 150 enviados
    assert lifted.price == 1010
    level = level_of(engine, Side.BUY, Ticks(1010))
    assert level.head is peg  # continua à frente da limit que provocou a mudança
    assert level.total_quantity == 400
    assert peg.remaining == 100


@pytest.mark.parametrize(
    ("reference", "side"),
    [(PegReference.BID, Side.SELL), (PegReference.OFFER, Side.BUY)],
    ids=["peg bid numa venda", "peg offer numa compra"],
)
def test_a_crossed_peg_is_rejected(reference: PegReference, side: Side) -> None:
    """Ela assumiria o preço do lado oposto: marketable ao nascer, e reprecificada para o
    novo topo oposto a cada movimento — uma market disfarçada num laço."""
    engine = MatchingEngine()

    with pytest.raises(InvalidOrderError, match="peg cruzado"):
        engine.submit_pegged(reference, side, 150)

    assert len(engine.book) == 0
    # a construção recusada não queima id nem número de sequência
    [accepted] = acceptances_of(engine.submit_limit(Side.BUY, Ticks(1000), 100))
    assert accepted.order_id == 1


def test_repricing_never_produces_a_trade() -> None:
    """A prova de terminação, como teste.

    O peg é homolateral, então o preço assumido é o topo do **próprio** lado, que pelo item 1
    dos invariantes é estritamente melhor que o topo oposto: a reprecificação não cruza o
    spread, e nenhum ``Trade`` sai dela. As duas limits do roteiro repousam sem executar, de
    modo que qualquer trade nestes eventos só poderia ter vindo da reconciliação.
    """
    engine = MatchingEngine()
    resting(engine, Side.BUY, Ticks(1000), 200)
    resting(engine, Side.SELL, Ticks(1100), 200)
    buy_peg = pegged(engine, Side.BUY, 150)
    sell_peg = pegged(engine, Side.SELL, 150)

    for side, price in [(Side.BUY, Ticks(1050)), (Side.SELL, Ticks(1060))]:
        events = engine.submit_limit(side, price, 100)

        assert trades_of(events) == []
        assert pegs_of(events) != []
        assert_no_pegged_only_level(engine)

    assert buy_peg.price == 1050
    assert sell_peg.price == 1060
    assert level_of(engine, Side.BUY, Ticks(1050)).head is buy_peg
    assert level_of(engine, Side.SELL, Ticks(1060)).head is sell_peg


@pytest.mark.parametrize("side", SIDES)
def test_a_pegged_order_follows_the_top_of_its_own_side(side: Side) -> None:
    engine = MatchingEngine()
    resting(engine, side, Ticks(1000), 200)
    resting(engine, side.opposite, improved(side, Ticks(1000), by=500), 200)
    peg = pegged(engine, side, 150)
    assert peg.price == 1000

    engine.submit_limit(side, improved(side, Ticks(1000)), 300)

    assert peg.price == improved(side, Ticks(1000))
    assert top_of(engine, side) == improved(side, Ticks(1000))
    assert level_of(engine, side, improved(side, Ticks(1000))).head is peg


def test_no_level_is_left_holding_only_pegged_orders() -> None:
    """Item 7 dos invariantes, na forma em que ele se apresenta em repouso: **nenhum**.

    O "no máximo um" do contrato descreve a janela transitória que ``best_non_pegged_price``
    precisa saber ler — o instante entre a saída da última não-pegged de um nível e a
    reconciliação que tira as pegged de lá. Ao fim de cada comando essa janela já fechou, e é
    por isso que o que se verifica aqui é a forma mais forte. O roteiro abre a janela três
    vezes: cancelando a não-pegged que dá referência, executando-a, e esvaziando o lado.
    """
    engine = MatchingEngine()
    first = resting(engine, Side.BUY, Ticks(1000), 200)
    second = resting(engine, Side.BUY, Ticks(990), 100)
    pegged(engine, Side.BUY, 150)
    script: list[Callable[[], list[Event]]] = [
        lambda: engine.submit_limit(Side.BUY, Ticks(1010), 300),
        lambda: engine.cancel(first.order_id),
        lambda: engine.submit_market(Side.SELL, 300),
        lambda: engine.cancel(second.order_id),
        lambda: engine.submit_limit(Side.BUY, Ticks(1020), 100),
    ]

    for command in script:
        command()
        assert_no_pegged_only_level(engine)


def test_the_book_is_never_left_crossed_with_pegged_orders() -> None:
    engine = MatchingEngine()
    script: list[Callable[[], list[Event]]] = [
        lambda: engine.submit_limit(Side.BUY, Ticks(1000), 200),
        lambda: engine.submit_limit(Side.SELL, Ticks(1100), 200),
        lambda: engine.submit_pegged(PegReference.BID, Side.BUY, 150),
        lambda: engine.submit_pegged(PegReference.OFFER, Side.SELL, 150),
        lambda: engine.submit_limit(Side.BUY, Ticks(1050), 100),
        lambda: engine.submit_limit(Side.SELL, Ticks(1060), 100),
        lambda: engine.submit_market(Side.SELL, 500),  # varre o bid e desfaz a referência
        lambda: engine.submit_limit(Side.BUY, Ticks(1070), 300),  # cruza o que restou da offer
        lambda: engine.submit_market(Side.BUY, 900),
        lambda: engine.submit_limit(Side.SELL, Ticks(1000), 100),
    ]

    for command in script:
        command()

        best_bid, best_ask = engine.book.best_bid, engine.book.best_ask
        assert best_bid is None or best_ask is None or best_bid < best_ask
        assert_no_pegged_only_level(engine)


def test_a_reentrant_reconciliation_fails_loudly() -> None:
    """Tripwire da prova de terminação, e não defesa contra um caminho existente.

    Hoje nada na reconciliação chama de volta a engine — é exatamente isso que a prova
    afirma —, então a condição precisa ser forçada de fora para ser exercitada. Se um dia ela
    passar a acontecer sozinha, a prova estará quebrada, e falhar alto é melhor do que um
    laço infinito ou uma cascata que termina por sorte.
    """
    engine = MatchingEngine()
    engine._reconciling = True

    with pytest.raises(RuntimeError, match="reentrante"):
        engine.submit_limit(Side.BUY, Ticks(1000), 100)


def test_snapshot_of_an_empty_book_has_no_entries() -> None:
    snapshot = snapshot_of(MatchingEngine())

    assert snapshot.bids == ()
    assert snapshot.asks == ()


def test_snapshot_lists_both_sides_by_price_then_by_queue_position() -> None:
    """Melhor preço primeiro, e dentro do nível a fila FIFO — duas ordens no mesmo preço
    aparecem separadas, que é o que torna a prioridade visível."""
    engine = MatchingEngine()
    resting(engine, Side.BUY, Ticks(990), 100)
    resting(engine, Side.BUY, Ticks(1000), 200)
    resting(engine, Side.BUY, Ticks(1000), 50)
    resting(engine, Side.SELL, Ticks(1020), 300)
    resting(engine, Side.SELL, Ticks(1010), 100)

    snapshot = snapshot_of(engine)

    assert snapshot.bids == (
        BookEntry(quantity=200, price=Ticks(1000)),
        BookEntry(quantity=50, price=Ticks(1000)),
        BookEntry(quantity=100, price=Ticks(990)),
    )
    assert snapshot.asks == (
        BookEntry(quantity=100, price=Ticks(1010)),
        BookEntry(quantity=300, price=Ticks(1020)),
    )


def test_snapshot_shows_the_remaining_quantity_not_the_original() -> None:
    engine = MatchingEngine()
    maker = resting(engine, Side.SELL, Ticks(2000), 100)
    engine.submit_market(Side.BUY, 40)

    snapshot = snapshot_of(engine)

    assert maker.quantity == 100
    assert snapshot.asks == (BookEntry(quantity=60, price=Ticks(2000)),)


def test_snapshot_is_a_value_and_not_a_view_of_the_book() -> None:
    """Formatado depois, uma vista mostraria o livro do momento da formatação."""
    engine = MatchingEngine()
    resting(engine, Side.SELL, Ticks(2000), 100)
    taken = snapshot_of(engine)

    engine.submit_market(Side.BUY, 100)
    resting(engine, Side.BUY, Ticks(1000), 50)

    assert taken.asks == (BookEntry(quantity=100, price=Ticks(2000)),)
    assert taken.bids == ()
    assert snapshot_of(engine) == BookSnapshot(
        bids=(BookEntry(quantity=50, price=Ticks(1000)),), asks=()
    )


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
