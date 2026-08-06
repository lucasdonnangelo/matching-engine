from collections.abc import Sequence

import pytest

from matching_engine.domain.engine import MatchingEngine, OrderNotFoundError
from matching_engine.domain.events import (
    BookEntry,
    BookSnapshot,
    Event,
    OrderAccepted,
    OrderAmended,
    OrderCancelled,
    Trade,
)
from matching_engine.domain.order import Order, OrderId
from matching_engine.domain.price import Ticks
from matching_engine.domain.price_level import PriceLevel
from matching_engine.domain.side import Side

SIDES = list(Side)


def trades_of(events: Sequence[Event]) -> list[Trade]:
    return [event for event in events if isinstance(event, Trade)]


def acceptances_of(events: Sequence[Event]) -> list[OrderAccepted]:
    return [event for event in events if isinstance(event, OrderAccepted)]


def amendments_of(events: Sequence[Event]) -> list[OrderAmended]:
    return [event for event in events if isinstance(event, OrderAmended)]


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
