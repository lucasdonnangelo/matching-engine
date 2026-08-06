import pytest

from matching_engine.domain.order import Order, OrderId, OrderIntegrityError
from matching_engine.domain.order_queue import QueueIntegrityError
from matching_engine.domain.price import Ticks
from matching_engine.domain.price_level import LevelIntegrityError, PriceLevel
from matching_engine.domain.side import PegReference, Side

LEVEL_PRICE = Ticks(1000)


def make_order(order_id: int, *, price: Ticks = LEVEL_PRICE, quantity: int = 10) -> Order:
    """Ordem de compra; o nível não olha para o lado, só para o preço."""
    return Order(
        order_id=OrderId(order_id),
        sequence_id=order_id,
        side=Side.BUY,
        price=price,
        quantity=quantity,
    )


def make_pegged_order(order_id: int, *, price: Ticks = LEVEL_PRICE, quantity: int = 10) -> Order:
    """Pegged já reprecificada: tem preço, e por isso pertence a um nível."""
    return Order(
        order_id=OrderId(order_id),
        sequence_id=order_id,
        side=Side.BUY,
        price=price,
        quantity=quantity,
        peg_reference=PegReference.BID,
    )


def test_empty_level() -> None:
    level = PriceLevel(LEVEL_PRICE)

    assert level.price == LEVEL_PRICE
    assert level.total_quantity == 0
    assert level.is_empty
    assert level.head is None
    assert len(level) == 0
    assert list(level) == []


def test_add_sums_into_the_total() -> None:
    level = PriceLevel(LEVEL_PRICE)

    level.add(make_order(1, quantity=7))

    assert level.total_quantity == 7
    assert not level.is_empty
    assert len(level) == 1


def test_successive_adds_accumulate() -> None:
    level = PriceLevel(LEVEL_PRICE)
    orders = [make_order(1, quantity=7), make_order(2, quantity=3), make_order(3, quantity=10)]

    for order in orders:
        level.add(order)

    assert level.total_quantity == 20
    assert len(level) == 3
    assert list(level) == orders


@pytest.mark.parametrize("price", [Ticks(999), Ticks(1001), Ticks(2000)])
def test_add_rejects_an_order_priced_elsewhere(price: Ticks) -> None:
    """O livro lê preço do nível e quantidade da soma: ordem alojada errado é anunciada errado."""
    level = PriceLevel(LEVEL_PRICE)
    resident = make_order(1, quantity=7)
    level.add(resident)

    with pytest.raises(LevelIntegrityError, match="não pertence ao nível"):
        level.add(make_order(2, price=price))

    assert level.total_quantity == 7  # a operação recusada não deixa rastro
    assert list(level) == [resident]


def test_level_integrity_error_is_a_runtime_error() -> None:
    """Rotear ordem até o nível é escolha da engine: violação aqui é bug, não entrada."""
    assert issubclass(LevelIntegrityError, RuntimeError)
    assert not issubclass(LevelIntegrityError, ValueError)


def test_remove_subtracts_from_the_total() -> None:
    level = PriceLevel(LEVEL_PRICE)
    first, second = make_order(1, quantity=7), make_order(2, quantity=3)
    level.add(first)
    level.add(second)

    level.remove(first)

    assert level.total_quantity == 3
    assert list(level) == [second]


def test_remove_subtracts_only_what_is_left_of_a_partially_filled_order() -> None:
    """O que a ordem ocupa no nível é o remanescente; o executado já baixou no ``fill``."""
    level = PriceLevel(LEVEL_PRICE)
    order = make_order(1, quantity=10)
    level.add(order)
    level.fill(order, 4)

    level.remove(order)

    assert level.total_quantity == 0
    assert level.is_empty


def test_partial_fill_lowers_the_total_and_the_remaining_together() -> None:
    level = PriceLevel(LEVEL_PRICE)
    order = make_order(1, quantity=10)
    level.add(order)

    level.fill(order, 4)

    assert order.remaining == 6
    assert level.total_quantity == 6
    assert len(level) == 1


def test_filled_order_stays_in_the_level() -> None:
    """Zerar não retira: a mesma baixa precisa acontecer no índice global, e é do chamador."""
    level = PriceLevel(LEVEL_PRICE)
    order = make_order(1, quantity=10)
    level.add(order)

    level.fill(order, 10)

    assert order.remaining == 0
    assert order.is_filled
    assert level.total_quantity == 0
    assert level.head is order
    assert not level.is_empty
    assert len(level) == 1


def test_rejected_fill_leaves_the_total_untouched() -> None:
    level = PriceLevel(LEVEL_PRICE)
    order = make_order(1, quantity=10)
    level.add(order)

    with pytest.raises(OrderIntegrityError, match="excede o remanescente"):
        level.fill(order, 11)

    assert level.total_quantity == 10
    assert order.remaining == 10


def test_fill_rejects_an_order_from_another_level() -> None:
    """Executar aqui o que vive noutro nível faria os dois totais mentirem ao mesmo tempo."""
    level = PriceLevel(LEVEL_PRICE)
    resident = make_order(1, quantity=10)
    level.add(resident)

    other = PriceLevel(Ticks(2000))
    stranger = make_order(2, price=Ticks(2000), quantity=8)
    other.add(stranger)

    with pytest.raises(LevelIntegrityError, match="não pertence ao nível"):
        level.fill(stranger, 5)

    assert level.total_quantity == 10
    assert len(level) == 1
    assert other.total_quantity == 8
    assert len(other) == 1
    assert stranger.remaining == 8  # a execução recusada não chegou à ordem


def test_fill_rejects_an_order_that_belongs_to_no_level() -> None:
    level = PriceLevel(LEVEL_PRICE)
    homeless = make_order(1, quantity=10)

    with pytest.raises(LevelIntegrityError, match="não pertence ao nível"):
        level.fill(homeless, 5)

    assert homeless.queue is None
    assert homeless.remaining == 10
    assert level.total_quantity == 0
    assert level.is_empty


def test_add_partial_fill_and_remove_return_the_total_to_zero() -> None:
    level = PriceLevel(LEVEL_PRICE)
    order = make_order(1, quantity=10)

    level.add(order)
    level.fill(order, 3)
    level.remove(order)

    assert level.total_quantity == 0
    assert level.is_empty
    assert level.head is None
    assert list(level) == []


def test_reduce_lowers_the_total_by_the_difference() -> None:
    level = PriceLevel(LEVEL_PRICE)
    first, second = make_order(1, quantity=10), make_order(2, quantity=7)
    level.add(first)
    level.add(second)

    level.reduce(first, 4)

    assert first.remaining == 4
    assert first.quantity == 4  # nada foi executado: a ordem passa a ser de 4
    assert level.total_quantity == 11


def test_reduce_keeps_the_position_in_the_queue() -> None:
    """É a diferença entre reduzir e reenfileirar: encolher não custa lugar na fila."""
    level = PriceLevel(LEVEL_PRICE)
    orders = [make_order(i) for i in range(1, 4)]
    for order in orders:
        level.add(order)

    level.reduce(orders[0], 2)

    assert level.head is orders[0]
    assert list(level) == orders
    assert len(level) == 3


def test_reduce_preserves_what_was_already_executed() -> None:
    level = PriceLevel(LEVEL_PRICE)
    order = make_order(1, quantity=10)
    level.add(order)
    level.fill(order, 4)

    level.reduce(order, 3)

    assert order.quantity == 7  # 3 por executar mais os 4 que já executaram
    assert order.remaining == 3
    assert order.quantity - order.remaining == 4
    assert level.total_quantity == 3


@pytest.mark.parametrize("new_remaining", [0, -1, 10, 11])
def test_reduce_rejects_a_new_balance_that_does_not_shrink(new_remaining: int) -> None:
    """Zerar uma ordem é executá-la ou cancelá-la; crescer é reenfileirá-la. Nem um nem outro."""
    level = PriceLevel(LEVEL_PRICE)
    order = make_order(1, quantity=10)
    level.add(order)

    with pytest.raises(LevelIntegrityError, match="redução inválida"):
        level.reduce(order, new_remaining)

    assert order.quantity == 10  # a redução recusada não deixa rastro
    assert order.remaining == 10
    assert level.total_quantity == 10


def test_reduce_rejects_an_order_from_another_level() -> None:
    level = PriceLevel(LEVEL_PRICE)
    resident = make_order(1, quantity=10)
    level.add(resident)

    other = PriceLevel(Ticks(2000))
    stranger = make_order(2, price=Ticks(2000), quantity=8)
    other.add(stranger)

    with pytest.raises(LevelIntegrityError, match="não pertence ao nível"):
        level.reduce(stranger, 5)

    assert level.total_quantity == 10
    assert other.total_quantity == 8
    assert stranger.remaining == 8


def test_amend_to_is_rejected_while_the_order_is_in_a_level() -> None:
    """Alterar no lugar dessincronizaria o total: o nível soma remanescentes que ele guarda."""
    level = PriceLevel(LEVEL_PRICE)
    order = make_order(1, quantity=10)
    level.add(order)

    with pytest.raises(OrderIntegrityError, match="está enfileirada"):
        order.amend_to(Ticks(2000), 5)

    assert order.price == LEVEL_PRICE
    assert order.quantity == 10
    assert order.remaining == 10
    assert level.total_quantity == 10
    assert list(level) == [order]


def test_head_is_always_the_oldest_order() -> None:
    level = PriceLevel(LEVEL_PRICE)
    orders = [make_order(i) for i in range(1, 4)]

    for order in orders:
        level.add(order)
        assert level.head is orders[0]

    level.fill(orders[0], 10)
    assert level.head is orders[0]  # zerar não muda a frente da fila

    for expected in orders:
        assert level.head is expected
        level.remove(expected)

    assert level.head is None


def test_empty_level_counts_no_non_pegged_order() -> None:
    assert PriceLevel(LEVEL_PRICE).non_pegged_count == 0


def test_non_pegged_count_rises_with_each_add() -> None:
    level = PriceLevel(LEVEL_PRICE)

    for expected, order_id in enumerate(range(1, 4), start=1):
        level.add(make_order(order_id))
        assert level.non_pegged_count == expected


def test_add_of_a_pegged_order_leaves_the_count_alone() -> None:
    """A pegged não é referência de ninguém: ela toma preço, não o dita."""
    level = PriceLevel(LEVEL_PRICE)
    level.add(make_order(1))

    level.add(make_pegged_order(2))

    assert len(level) == 2
    assert level.total_quantity == 20  # a pegged conta na quantidade, só não na referência
    assert level.non_pegged_count == 1


def test_a_level_of_only_pegged_orders_counts_zero() -> None:
    """É esta a leitura O(1) que identifica o nível pegged-only do item 7 dos invariantes."""
    level = PriceLevel(LEVEL_PRICE)
    level.add(make_pegged_order(1))
    level.add(make_pegged_order(2))

    assert not level.is_empty
    assert len(level) == 2
    assert level.non_pegged_count == 0


def test_non_pegged_count_falls_with_each_remove() -> None:
    level = PriceLevel(LEVEL_PRICE)
    orders = [make_order(order_id) for order_id in range(1, 4)]
    for order in orders:
        level.add(order)

    for expected, order in enumerate(orders, start=1):
        level.remove(order)
        assert level.non_pegged_count == 3 - expected


def test_remove_of_a_pegged_order_leaves_the_count_alone() -> None:
    level = PriceLevel(LEVEL_PRICE)
    resident = make_order(1)
    pegged = make_pegged_order(2)
    level.add(resident)
    level.add(pegged)

    level.remove(pegged)

    assert level.non_pegged_count == 1
    assert list(level) == [resident]


def test_fill_does_not_change_the_non_pegged_count() -> None:
    """Executar muda quantidade, não composição: a ordem zerada continua no nível."""
    level = PriceLevel(LEVEL_PRICE)
    order = make_order(1, quantity=10)
    level.add(order)

    level.fill(order, 4)
    assert level.non_pegged_count == 1

    level.fill(order, 6)
    assert order.is_filled
    assert level.non_pegged_count == 1  # quem a retira é o chamador, e é lá que a conta baixa


def test_reduce_does_not_change_the_non_pegged_count() -> None:
    level = PriceLevel(LEVEL_PRICE)
    order = make_order(1, quantity=10)
    level.add(order)

    level.reduce(order, 3)

    assert level.non_pegged_count == 1


def test_a_rejected_add_does_not_change_the_non_pegged_count() -> None:
    level = PriceLevel(LEVEL_PRICE)
    level.add(make_order(1))

    with pytest.raises(LevelIntegrityError, match="não pertence ao nível"):
        level.add(make_order(2, price=Ticks(2000)))

    assert level.non_pegged_count == 1


def test_an_add_rejected_by_the_queue_does_not_change_the_non_pegged_count() -> None:
    """A conta sobe depois de a fila aceitar, e é por isso que a recusa dela também não conta."""
    level = PriceLevel(LEVEL_PRICE)
    resident = make_order(1)
    level.add(resident)
    twin_level = PriceLevel(LEVEL_PRICE)

    with pytest.raises(QueueIntegrityError, match="já está ligada a uma fila"):
        twin_level.add(resident)

    assert twin_level.non_pegged_count == 0
    assert level.non_pegged_count == 1


def test_long_mixed_sequence_keeps_the_non_pegged_count_in_step() -> None:
    """Conta mantida à mão desvia por acúmulo: só uma sequência longa expõe o desvio."""
    level = PriceLevel(LEVEL_PRICE)
    orders = [make_pegged_order(i) if i % 3 == 0 else make_order(i) for i in range(1, 41)]

    for index, order in enumerate(orders):
        level.add(order)
        if index % 4 == 0:
            level.fill(order, 4)
        if index % 5 == 0:
            level.reduce(order, 2)
        if index % 7 == 0:
            level.remove(order)

    for order in list(level)[:8]:
        level.remove(order)

    assert level.non_pegged_count == sum(1 for order in level if not order.is_pegged)
    assert level.non_pegged_count > 0  # a sequência não esvaziou as não-pegged por acidente
    assert len(level) > level.non_pegged_count  # nem as pegged


def test_long_mixed_sequence_keeps_the_total_in_step() -> None:
    """Total mantido à mão desvia por acúmulo: só uma sequência longa expõe o desvio."""
    level = PriceLevel(LEVEL_PRICE)
    orders = [make_order(i, quantity=10 + i % 5) for i in range(1, 41)]

    for index, order in enumerate(orders):
        level.add(order)
        if index % 3 == 0:
            level.fill(order, 4)
        if index % 5 == 0:
            level.remove(order)

    for order in list(level)[::2]:
        level.fill(order, 1)
    for order in list(level)[:5]:
        level.remove(order)

    assert level.total_quantity == sum(order.remaining for order in level)
    assert level.total_quantity > 0  # a sequência não esvaziou o nível por acidente
    assert len(level) == len(list(level))
