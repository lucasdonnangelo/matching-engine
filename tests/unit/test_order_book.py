import pytest

from matching_engine.domain.order import Order
from matching_engine.domain.order_book import BookIntegrityError, OrderBook
from matching_engine.domain.price import Ticks
from matching_engine.domain.side import PegReference, Side

SIDES = list(Side)


def top_of(book: OrderBook, side: Side) -> Ticks | None:
    return book.best_bid if side is Side.BUY else book.best_ask


def test_empty_book() -> None:
    book = OrderBook()

    assert book.best_bid is None
    assert book.best_ask is None
    assert len(book) == 0
    assert book.side(Side.BUY).is_empty
    assert book.side(Side.SELL).is_empty


@pytest.mark.parametrize("side", SIDES)
def test_side_and_opposite_side(side: Side) -> None:
    book = OrderBook()

    assert book.side(side).side is side
    assert book.opposite_side(side) is book.side(side.opposite)


def test_create_order_hands_out_growing_numbers_without_inserting() -> None:
    """Fabricar não é inserir: a agressiva pode executar por inteiro e nunca repousar."""
    book = OrderBook()

    first = book.create_order(Side.BUY, Ticks(1000), 10)
    second = book.create_order(Side.SELL, Ticks(1010), 5)

    assert second.order_id > first.order_id
    assert second.sequence_id > first.sequence_id
    assert len(book) == 0
    assert book.get(first.order_id) is None
    assert first.order_id not in book
    assert book.best_bid is None
    assert book.best_ask is None


@pytest.mark.parametrize(
    ("side", "peg_reference"),
    [(Side.BUY, PegReference.BID), (Side.SELL, PegReference.OFFER)],
)
def test_create_order_of_a_pegged_order_without_price_is_parked(
    side: Side, peg_reference: PegReference
) -> None:
    book = OrderBook()

    order = book.create_order(side, None, 10, peg_reference)

    assert order.is_pegged
    assert order.is_parked
    assert order.price is None
    assert len(book) == 0


@pytest.mark.parametrize("side", SIDES)
def test_add_routes_the_order_to_its_own_side(side: Side) -> None:
    book = OrderBook()
    order = book.create_order(side, Ticks(1000), 10)

    book.add(order)

    assert top_of(book, side) == 1000
    assert top_of(book, side.opposite) is None
    assert len(book) == 1
    assert len(book.side(side)) == 1
    assert book.opposite_side(side).is_empty


def test_add_rejects_a_duplicate_order_id() -> None:
    book = OrderBook()
    order = book.create_order(Side.BUY, Ticks(1000), 10)
    book.add(order)

    twin = Order(
        order_id=order.order_id,
        sequence_id=book.next_sequence(),
        side=Side.BUY,
        price=Ticks(1010),
        quantity=5,
    )

    with pytest.raises(BookIntegrityError, match="já está no índice global"):
        book.add(twin)

    assert len(book) == 1
    assert book.get(order.order_id) is order
    # a recusa veio antes de o lado ser tocado: o preço da intrusa não virou nível
    assert book.side(Side.BUY).level_at(Ticks(1010)) is None
    assert len(book.side(Side.BUY)) == 1


def test_re_adding_a_live_order_is_rejected() -> None:
    book = OrderBook()
    order = book.create_order(Side.BUY, Ticks(1000), 10)
    book.add(order)

    with pytest.raises(BookIntegrityError, match="já está no índice global"):
        book.add(order)

    assert len(book) == 1
    assert len(book.side(Side.BUY)) == 1


def test_book_integrity_error_is_a_runtime_error() -> None:
    """O id é gerado pelo próprio livro: repeti-lo é bug da engine, não entrada."""
    assert issubclass(BookIntegrityError, RuntimeError)
    assert not issubclass(BookIntegrityError, ValueError)


@pytest.mark.parametrize("side", SIDES)
def test_get_and_contains_follow_the_life_of_the_order(side: Side) -> None:
    book = OrderBook()
    order = book.create_order(side, Ticks(1000), 10)

    book.add(order)
    assert book.get(order.order_id) is order
    assert order.order_id in book

    book.remove(order)
    assert book.get(order.order_id) is None
    assert order.order_id not in book
    assert len(book) == 0


@pytest.mark.parametrize("side", SIDES)
def test_remove_takes_the_emptied_level_with_it(side: Side) -> None:
    """As três baixas numa operação só: nível, índice de preços e índice global."""
    book = OrderBook()
    order = book.create_order(side, Ticks(1000), 10)
    book.add(order)

    book.remove(order)

    assert len(book) == 0
    assert len(book.side(side)) == 0
    assert book.side(side).level_at(Ticks(1000)) is None
    assert book.side(side).is_empty
    assert top_of(book, side) is None


@pytest.mark.parametrize("side", SIDES)
def test_remove_keeps_a_level_that_still_has_orders(side: Side) -> None:
    book = OrderBook()
    first = book.create_order(side, Ticks(1000), 10)
    second = book.create_order(side, Ticks(1000), 4)
    book.add(first)
    book.add(second)

    book.remove(first)

    level = book.side(side).level_at(Ticks(1000))
    assert level is not None
    assert len(book.side(side)) == 1
    assert level.total_quantity == 4
    assert list(level) == [second]
    assert len(book) == 1
    assert top_of(book, side) == 1000


def test_remove_rejects_an_order_that_is_not_in_the_index() -> None:
    book = OrderBook()
    resident = book.create_order(Side.BUY, Ticks(1000), 10)
    book.add(resident)
    stranger = book.create_order(Side.BUY, Ticks(1000), 10)

    with pytest.raises(BookIntegrityError, match="não está no índice global"):
        book.remove(stranger)

    assert len(book) == 1
    assert list(book.side(Side.BUY)) == [book.side(Side.BUY).best_level]


def test_sequence_numbers_are_handed_out_once_and_in_order() -> None:
    book = OrderBook()

    handed = [
        book.create_order(Side.BUY, Ticks(1000), 10).sequence_id,
        book.next_sequence(),
        book.next_sequence(),
        book.create_order(Side.SELL, Ticks(1010), 10).sequence_id,
        book.next_sequence(),
    ]

    assert handed == sorted(handed)
    assert len(set(handed)) == len(handed)


def test_the_two_counters_run_independently() -> None:
    """O amend consome prioridade sem consumir identidade; um contador só faria o id saltar."""
    book = OrderBook()
    first = book.create_order(Side.BUY, Ticks(1000), 10)

    for _ in range(5):
        book.next_sequence()
    second = book.create_order(Side.BUY, Ticks(1000), 10)

    assert second.order_id == first.order_id + 1
    assert second.sequence_id == first.sequence_id + 6


def test_both_sides_populated() -> None:
    book = OrderBook()
    for price in [Ticks(990), Ticks(1000), Ticks(980)]:
        book.add(book.create_order(Side.BUY, price, 10))
    for price in [Ticks(1020), Ticks(1010), Ticks(1030)]:
        book.add(book.create_order(Side.SELL, price, 10))

    assert book.best_bid == 1000
    assert book.best_ask == 1010
    assert len(book) == 6
    assert len(book.side(Side.BUY)) == 3
    assert len(book.side(Side.SELL)) == 3


def test_a_crossed_book_is_constructible_by_hand() -> None:
    """Item 1 dos invariantes é do matching: bid 10.5 acima de ask 10 é montável aqui."""
    book = OrderBook()

    book.add(book.create_order(Side.BUY, Ticks(1050), 10))
    book.add(book.create_order(Side.SELL, Ticks(1000), 10))

    assert book.best_bid == 1050
    assert book.best_ask == 1000
    assert book.best_bid > book.best_ask


@pytest.mark.parametrize("side", SIDES)
def test_orders_at_the_same_price_keep_fifo(side: Side) -> None:
    book = OrderBook()
    orders = [book.create_order(side, Ticks(1000), 10) for _ in range(3)]
    for order in orders:
        book.add(order)

    level = book.side(side).level_at(Ticks(1000))
    assert level is not None
    assert list(level) == orders
    assert level.head is orders[0]
    assert [order.sequence_id for order in level] == sorted(order.sequence_id for order in level)


def test_emptying_the_book_returns_it_to_the_initial_state() -> None:
    book = OrderBook()
    orders = [
        book.create_order(Side.BUY, Ticks(1000), 10),
        book.create_order(Side.BUY, Ticks(990), 10),
        book.create_order(Side.SELL, Ticks(1010), 10),
        book.create_order(Side.SELL, Ticks(1020), 10),
    ]
    for order in orders:
        book.add(order)

    for order in orders:
        book.remove(order)

    assert len(book) == 0
    assert book.best_bid is None
    assert book.best_ask is None
    assert book.side(Side.BUY).is_empty
    assert book.side(Side.SELL).is_empty
    assert len(book.side(Side.BUY)) == 0
    assert len(book.side(Side.SELL)) == 0
    assert all(order.order_id not in book for order in orders)
