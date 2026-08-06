from collections.abc import Sequence

import pytest

from matching_engine.domain.book_side import BookSide
from matching_engine.domain.order import Order, OrderId
from matching_engine.domain.price import Ticks, parse_price
from matching_engine.domain.price_level import LevelIntegrityError
from matching_engine.domain.side import PegReference, Side

SIDES = list(Side)


def make_order(order_id: int, price: Ticks, *, side: Side = Side.BUY, quantity: int = 10) -> Order:
    return Order(
        order_id=OrderId(order_id),
        sequence_id=order_id,
        side=side,
        price=price,
        quantity=quantity,
    )


def make_pegged_order(
    order_id: int, price: Ticks, *, side: Side = Side.BUY, quantity: int = 10
) -> Order:
    """Pegged já reprecificada: tem preço, e por isso pertence a um nível."""
    return Order(
        order_id=OrderId(order_id),
        sequence_id=order_id,
        side=side,
        price=price,
        quantity=quantity,
        peg_reference=PegReference.BID if side is Side.BUY else PegReference.OFFER,
    )


def make_side(side: Side, prices: Sequence[Ticks]) -> BookSide:
    """Um nível por preço, uma ordem por nível, na ordem de inserção recebida."""
    book_side = BookSide(side)
    for order_id, price in enumerate(prices, start=1):
        book_side.add(make_order(order_id, price, side=side))
    return book_side


def top_price(side: Side, prices: Sequence[Ticks], *, ahead_by: int = 10) -> Ticks:
    """Preço um passo à frente do topo — onde as pegged do lado repousam."""
    best = best_of(side, prices)
    return Ticks(best + ahead_by) if side is Side.BUY else Ticks(best - ahead_by)


def best_of(side: Side, prices: Sequence[Ticks]) -> Ticks:
    return max(prices) if side is Side.BUY else min(prices)


def by_priority(side: Side, prices: Sequence[Ticks]) -> list[Ticks]:
    return sorted(prices, reverse=side is Side.BUY)


@pytest.mark.parametrize("side", SIDES)
def test_empty_side(side: Side) -> None:
    book_side = BookSide(side)

    assert book_side.side is side
    assert book_side.best_price is None
    assert book_side.best_level is None
    assert book_side.is_empty
    assert len(book_side) == 0
    assert list(book_side) == []


@pytest.mark.parametrize("side", SIDES)
def test_single_level(side: Side) -> None:
    book_side = make_side(side, [Ticks(1000)])

    assert book_side.best_price == 1000
    assert book_side.best_level is book_side.level_at(Ticks(1000))
    assert not book_side.is_empty
    assert len(book_side) == 1


@pytest.mark.parametrize("side", SIDES)
def test_best_price_of_levels_inserted_out_of_order(side: Side) -> None:
    """A ordem de chegada não conta: o topo é o maior preço no bid e o menor na offer."""
    prices = [Ticks(1000), Ticks(1050), Ticks(990), Ticks(1010)]

    book_side = make_side(side, prices)

    assert book_side.best_price == best_of(side, prices)
    assert len(book_side) == 4


@pytest.mark.parametrize("side", SIDES)
def test_iteration_goes_from_best_to_worst(side: Side) -> None:
    prices = [Ticks(1000), Ticks(1050), Ticks(990), Ticks(1010)]
    book_side = make_side(side, prices)

    assert [level.price for level in book_side] == by_priority(side, prices)


@pytest.mark.parametrize("side", SIDES)
def test_orders_at_the_same_price_share_the_level_and_keep_fifo(side: Side) -> None:
    book_side = BookSide(side)
    first = make_order(1, Ticks(1000), side=side, quantity=7)
    second = make_order(2, Ticks(1000), side=side, quantity=3)

    book_side.add(first)
    book_side.add(second)

    level = book_side.level_at(Ticks(1000))
    assert level is not None
    assert len(book_side) == 1
    assert level.total_quantity == 10
    assert level.head is first
    assert list(level) == [first, second]


@pytest.mark.parametrize(
    ("side", "peg_reference"),
    [(Side.BUY, PegReference.BID), (Side.SELL, PegReference.OFFER)],
)
def test_add_rejects_a_parked_order(side: Side, peg_reference: PegReference) -> None:
    """Sem preço não há nível: a pegged sem referência vive fora dos dois lados."""
    book_side = BookSide(side)
    parked = Order(
        order_id=OrderId(1),
        sequence_id=1,
        side=side,
        price=None,
        quantity=10,
        peg_reference=peg_reference,
    )

    with pytest.raises(LevelIntegrityError, match="parked"):
        book_side.add(parked)

    assert book_side.is_empty
    assert len(book_side) == 0


@pytest.mark.parametrize("side", SIDES)
def test_add_rejects_an_order_from_the_opposite_side(side: Side) -> None:
    """Ninguém mais confere: sem esta guarda o lado criaria nível e enfileiraria calado."""
    book_side = make_side(side, [Ticks(1000)])
    intruder = make_order(99, Ticks(1050), side=side.opposite)

    with pytest.raises(LevelIntegrityError, match=f"não do lado {side.name}"):
        book_side.add(intruder)

    assert len(book_side) == 1
    assert book_side.best_price == 1000
    assert book_side.level_at(Ticks(1050)) is None  # a recusa não deixou nível para trás
    assert intruder.queue is None


@pytest.mark.parametrize("side", SIDES)
def test_remove_rejects_an_order_from_the_opposite_side_without_a_level_here(side: Side) -> None:
    book_side = make_side(side, [Ticks(1000)])

    with pytest.raises(LevelIntegrityError, match=f"não do lado {side.name}"):
        book_side.remove(make_order(99, Ticks(1050), side=side.opposite))

    assert len(book_side) == 1
    assert book_side.best_price == 1000


@pytest.mark.parametrize("side", SIDES)
def test_remove_rejects_an_order_from_the_opposite_side_that_has_a_level_here(side: Side) -> None:
    """O caso que passava adiante: com nível no preço, a recusa vinha da fila, por outro motivo.

    Os dois lados guardam ordens a 10 — o que num livro ativo é o encontro dos dois topos
    — e a ordem retirada é a do lado oposto, viva no lado dela.
    """
    book_side = make_side(side, [Ticks(1000)])
    opposite = make_side(side.opposite, [Ticks(1000)])
    resident = opposite.best_level
    assert resident is not None
    intruder = resident.head
    assert intruder is not None

    with pytest.raises(LevelIntegrityError, match=f"não do lado {side.name}"):
        book_side.remove(intruder)

    level = book_side.level_at(Ticks(1000))
    assert level is not None
    assert len(book_side) == 1
    assert level.total_quantity == 10
    assert len(opposite) == 1
    assert resident.total_quantity == 10
    assert list(resident) == [intruder]
    assert intruder.queue is not None


@pytest.mark.parametrize("side", SIDES)
def test_level_at_does_not_create_the_level(side: Side) -> None:
    book_side = make_side(side, [Ticks(1000)])

    assert book_side.level_at(Ticks(1050)) is None
    assert len(book_side) == 1  # a consulta não deixou nível vazio para trás


@pytest.mark.parametrize("side", SIDES)
def test_level_for_creates_once_and_reuses(side: Side) -> None:
    book_side = BookSide(side)

    created = book_side.level_for(Ticks(1000))

    assert created.price == 1000
    assert created.is_empty
    assert book_side.level_for(Ticks(1000)) is created
    assert book_side.level_at(Ticks(1000)) is created
    assert len(book_side) == 1


@pytest.mark.parametrize("side", SIDES)
def test_remove_keeps_the_emptied_level_in_the_index(side: Side) -> None:
    """Tirar a última ordem não tira o nível: essa baixa é do chamador, e é explícita."""
    book_side = BookSide(side)
    order = make_order(1, Ticks(1000), side=side)
    book_side.add(order)

    book_side.remove(order)

    level = book_side.level_at(Ticks(1000))
    assert level is not None
    assert level.is_empty
    assert len(book_side) == 1
    assert not book_side.is_empty
    assert book_side.best_price == 1000  # nível fantasma enquanto ninguém o retira


@pytest.mark.parametrize("side", SIDES)
def test_remove_rejects_an_order_that_is_not_in_this_side(side: Side) -> None:
    book_side = make_side(side, [Ticks(1000)])

    with pytest.raises(LevelIntegrityError, match="não está em nenhum nível"):
        book_side.remove(make_order(99, Ticks(1050), side=side))

    assert len(book_side) == 1


@pytest.mark.parametrize("side", SIDES)
def test_remove_if_empty_drops_the_level_and_promotes_the_next(side: Side) -> None:
    prices = [Ticks(1000), Ticks(1050), Ticks(990)]
    book_side = make_side(side, prices)
    best = book_side.best_level
    assert best is not None
    order = best.head
    assert order is not None

    book_side.remove(order)
    book_side.remove_if_empty(best)

    remaining = [price for price in prices if price != best.price]
    assert book_side.level_at(best.price) is None
    assert len(book_side) == 2
    assert book_side.best_price == best_of(side, remaining)


@pytest.mark.parametrize("side", SIDES)
def test_remove_if_empty_keeps_a_level_that_still_has_orders(side: Side) -> None:
    book_side = BookSide(side)
    first = make_order(1, Ticks(1000), side=side)
    second = make_order(2, Ticks(1000), side=side)
    book_side.add(first)
    book_side.add(second)
    level = book_side.level_at(Ticks(1000))
    assert level is not None

    book_side.remove(first)
    book_side.remove_if_empty(level)

    assert book_side.level_at(Ticks(1000)) is level
    assert len(book_side) == 1
    assert list(level) == [second]


@pytest.mark.parametrize("side", SIDES)
def test_removing_a_middle_level_leaves_the_best_price_alone(side: Side) -> None:
    prices = [Ticks(990), Ticks(1000), Ticks(1010)]
    book_side = make_side(side, prices)
    middle = book_side.level_at(Ticks(1000))
    assert middle is not None
    order = middle.head
    assert order is not None

    book_side.remove(order)
    book_side.remove_if_empty(middle)

    assert len(book_side) == 2
    assert book_side.best_price == best_of(side, prices)
    assert [level.price for level in book_side] == by_priority(side, [Ticks(990), Ticks(1010)])


@pytest.mark.parametrize("side", SIDES)
def test_emptying_every_level_leaves_the_side_coherent(side: Side) -> None:
    prices = [Ticks(1000), Ticks(1050), Ticks(990)]
    book_side = make_side(side, prices)

    for level in list(book_side):
        for order in list(level):
            book_side.remove(order)
        book_side.remove_if_empty(level)

    assert book_side.is_empty
    assert book_side.best_price is None
    assert book_side.best_level is None
    assert len(book_side) == 0
    assert list(book_side) == []


@pytest.mark.parametrize("side", SIDES)
def test_best_non_pegged_price_of_an_empty_side_is_none(side: Side) -> None:
    assert BookSide(side).best_non_pegged_price is None


@pytest.mark.parametrize("side", SIDES)
def test_best_non_pegged_price_is_the_top_when_no_level_is_pegged_only(side: Side) -> None:
    prices = [Ticks(1000), Ticks(1050), Ticks(990), Ticks(1010)]

    book_side = make_side(side, prices)

    assert book_side.best_non_pegged_price == best_of(side, prices)
    assert book_side.best_non_pegged_price == book_side.best_price


@pytest.mark.parametrize("side", SIDES)
def test_best_non_pegged_price_takes_a_top_level_that_mixes_the_two(side: Side) -> None:
    """Basta uma não-pegged no nível: o que desqualifica o topo é não ter nenhuma."""
    prices = [Ticks(1000), Ticks(1010)]
    book_side = make_side(side, prices)

    book_side.add(make_pegged_order(99, best_of(side, prices), side=side))

    assert book_side.best_non_pegged_price == best_of(side, prices)


@pytest.mark.parametrize("side", SIDES)
def test_best_non_pegged_price_skips_a_pegged_only_top_level(side: Side) -> None:
    """O caso que a consulta existe para resolver: o topo é das pegged, a referência é o de trás."""
    prices = [Ticks(990), Ticks(1000), Ticks(1010)]
    book_side = make_side(side, prices)
    pegged_price = top_price(side, prices)
    book_side.add(make_pegged_order(98, pegged_price, side=side))
    book_side.add(make_pegged_order(99, pegged_price, side=side))

    assert book_side.best_price == pegged_price
    assert book_side.best_non_pegged_price == best_of(side, prices)


@pytest.mark.parametrize("side", SIDES)
def test_best_non_pegged_price_of_a_side_with_only_a_pegged_level_is_none(side: Side) -> None:
    """Item 7 intacto — o único nível é o topo —, e mesmo assim não há referência nenhuma."""
    book_side = BookSide(side)
    book_side.add(make_pegged_order(1, Ticks(1000), side=side))

    assert book_side.best_price == 1000
    assert book_side.best_non_pegged_price is None


@pytest.mark.parametrize("side", SIDES)
def test_best_non_pegged_price_rejects_two_pegged_only_levels(side: Side) -> None:
    """Todo o lado só de pegged: elas se espalharam por dois preços, o que o item 7 proíbe."""
    book_side = BookSide(side)
    book_side.add(make_pegged_order(1, Ticks(1000), side=side))
    book_side.add(make_pegged_order(2, Ticks(1010), side=side))

    with pytest.raises(LevelIntegrityError, match="item 7 dos invariantes"):
        _ = book_side.best_non_pegged_price


@pytest.mark.parametrize("side", SIDES)
def test_best_non_pegged_price_fails_instead_of_descending_to_a_third_level(side: Side) -> None:
    """Há resposta um nível abaixo, e é por isso mesmo que a busca não continua.

    Devolvê-la mascararia a violação e faria a consulta descer o lado inteiro, que é a
    varredura O(P) que a seção 5 do contrato proíbe.
    """
    prices = [Ticks(1000)]
    book_side = make_side(side, prices)
    book_side.add(make_pegged_order(98, top_price(side, prices), side=side))
    book_side.add(make_pegged_order(99, top_price(side, prices, ahead_by=20), side=side))

    with pytest.raises(LevelIntegrityError, match="item 7 dos invariantes"):
        _ = book_side.best_non_pegged_price


@pytest.mark.parametrize("side", SIDES)
def test_best_non_pegged_price_follows_the_level_losing_its_last_non_pegged_order(
    side: Side,
) -> None:
    """A conta é do nível, e a consulta só a lê: esvaziar as não-pegged do topo muda a resposta."""
    prices = [Ticks(1000), Ticks(1010)]
    book_side = make_side(side, prices)
    top = book_side.best_level
    assert top is not None
    resident = top.head
    assert resident is not None
    book_side.add(make_pegged_order(99, top.price, side=side))

    assert book_side.best_non_pegged_price == top.price

    book_side.remove(resident)

    behind = [price for price in prices if price != top.price]
    assert book_side.best_price == top.price  # a pegged segura o nível no índice
    assert book_side.best_non_pegged_price == best_of(side, behind)


@pytest.mark.parametrize("side", SIDES)
def test_sub_tick_prices_sort_by_value(side: Side) -> None:
    """Ordenação é por valor em ticks: "10.1" vem depois de "10", e "9.99" antes dos dois."""
    prices = [parse_price(text) for text in ["10.1", "9.98", "10", "10.5", "9.99"]]

    book_side = make_side(side, prices)

    assert [level.price for level in book_side] == by_priority(side, prices)
    assert book_side.best_price == best_of(side, prices)
