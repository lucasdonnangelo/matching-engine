import pytest

from matching_engine.domain.order import Order, OrderId
from matching_engine.domain.order_queue import OrderQueue, QueueIntegrityError
from matching_engine.domain.price import Ticks
from matching_engine.domain.side import Side


def make_order(order_id: int) -> Order:
    """A fila não olha para preço, lado nem quantidade: só o ``order_id`` distingue."""
    return Order(
        order_id=OrderId(order_id),
        sequence_id=order_id,
        side=Side.BUY,
        price=Ticks(1000),
        quantity=10,
    )


def make_queue(size: int) -> tuple[OrderQueue, list[Order]]:
    queue = OrderQueue()
    orders = [make_order(i) for i in range(1, size + 1)]
    for order in orders:
        queue.append(order)
    return queue, orders


def assert_queue_is(queue: OrderQueue, expected: list[Order]) -> None:
    """Confere a fila pelos dois sentidos: os elos só estão sãos se concordarem entre si.

    Percorrer apenas do ``head`` esconderia um ``prev`` desatualizado, que é justamente o
    ponteiro que uma remoção mal feita deixa apontando para uma ordem já retirada.
    """
    assert list(queue) == expected
    assert len(queue) == len(expected)
    assert queue.is_empty is (not expected)
    assert queue.head is (expected[0] if expected else None)

    if not expected:
        return

    assert expected[0].prev is None
    assert expected[-1].next is None

    backward: list[Order] = []
    node: Order | None = expected[-1]
    while node is not None:
        backward.append(node)
        node = node.prev
    assert backward == list(reversed(expected))


def test_empty_queue() -> None:
    assert_queue_is(OrderQueue(), [])


def test_append_preserves_fifo_order() -> None:
    """Prioridade temporal é a própria posição na fila: quem chega antes sai antes."""
    queue, orders = make_queue(3)

    assert_queue_is(queue, orders)


def test_appending_to_an_empty_queue_makes_the_order_both_ends() -> None:
    queue = OrderQueue()
    order = make_order(1)

    queue.append(order)

    assert_queue_is(queue, [order])
    assert order.prev is None
    assert order.next is None


def test_removing_the_only_order_empties_the_queue() -> None:
    queue, (only,) = make_queue(1)

    queue.remove(only)

    assert_queue_is(queue, [])


def test_removing_the_head_promotes_the_next_order() -> None:
    queue, orders = make_queue(3)

    queue.remove(orders[0])

    assert queue.head is orders[1]
    assert orders[1].prev is None
    assert_queue_is(queue, orders[1:])


def test_removing_the_tail_leaves_the_previous_order_as_the_end() -> None:
    queue, orders = make_queue(3)

    queue.remove(orders[2])

    assert orders[1].next is None
    assert_queue_is(queue, orders[:2])


def test_removing_from_the_middle_links_the_neighbours() -> None:
    queue, orders = make_queue(3)

    queue.remove(orders[1])

    assert orders[0].next is orders[2]
    assert orders[2].prev is orders[0]
    assert_queue_is(queue, [orders[0], orders[2]])


def test_removing_every_order_from_the_head() -> None:
    """Consumo do matching: a fila esvazia pela frente, um fill de cada vez."""
    queue, orders = make_queue(4)

    for index, order in enumerate(orders):
        queue.remove(order)
        assert_queue_is(queue, orders[index + 1 :])

    assert_queue_is(queue, [])


def test_removing_every_order_from_the_tail() -> None:
    queue, orders = make_queue(4)

    for index in reversed(range(len(orders))):
        queue.remove(orders[index])
        assert_queue_is(queue, orders[:index])

    assert_queue_is(queue, [])


def test_append_after_emptying_the_queue() -> None:
    """A fila esvaziada não guarda resíduo das pontas antigas."""
    queue, orders = make_queue(2)
    for order in orders:
        queue.remove(order)

    fresh = make_order(3)
    queue.append(fresh)

    assert_queue_is(queue, [fresh])


@pytest.mark.parametrize("position", [0, 1, 2], ids=["cabeça", "meio", "cauda"])
def test_removed_order_has_its_links_cleared(position: int) -> None:
    queue, orders = make_queue(3)

    queue.remove(orders[position])

    assert orders[position].prev is None
    assert orders[position].next is None


def test_removed_order_can_be_appended_again() -> None:
    """É o caminho do amend que perde prioridade: sai da fila e volta pelo fim."""
    queue, orders = make_queue(3)
    requeued = orders[0]

    queue.remove(requeued)
    queue.append(requeued)

    assert_queue_is(queue, [orders[1], orders[2], requeued])


def test_removed_order_can_be_appended_to_another_queue() -> None:
    queue, orders = make_queue(2)
    other = OrderQueue()

    queue.remove(orders[0])
    other.append(orders[0])

    assert_queue_is(queue, [orders[1]])
    assert_queue_is(other, [orders[0]])


@pytest.mark.parametrize(
    ("size", "position"),
    [(1, 0), (3, 0), (3, 1), (3, 2)],
    ids=["única da fila", "cabeça", "meio", "cauda"],
)
def test_append_rejects_an_order_already_in_the_queue(size: int, position: int) -> None:
    """Nenhuma posição escapa: a guarda consulta a alça, não a vizinhança da ordem."""
    queue, orders = make_queue(size)

    with pytest.raises(QueueIntegrityError, match="já está ligada"):
        queue.append(orders[position])

    assert_queue_is(queue, orders)  # a operação recusada não deixa rastro


@pytest.mark.parametrize("size", [0, 1, 3], ids=["fila vazia", "fila unitária", "fila cheia"])
def test_remove_rejects_an_order_that_was_never_appended(size: int) -> None:
    queue, orders = make_queue(size)
    stranger = make_order(99)

    with pytest.raises(QueueIntegrityError, match="não pertence"):
        queue.remove(stranger)

    assert_queue_is(queue, orders)


@pytest.mark.parametrize(
    ("other_size", "position"),
    [(1, 0), (3, 0), (3, 1), (3, 2)],
    ids=[
        "única da outra fila",
        "cabeça da outra fila",
        "miolo da outra fila",
        "cauda da outra fila",
    ],
)
def test_remove_rejects_an_order_from_another_queue(other_size: int, position: int) -> None:
    """Inclusive do miolo, que tem os dois vizinhos preenchidos como qualquer ordem desta."""
    queue, orders = make_queue(3)
    other, other_orders = make_queue(other_size)

    with pytest.raises(QueueIntegrityError, match="não pertence"):
        queue.remove(other_orders[position])

    assert_queue_is(queue, orders)
    assert_queue_is(other, other_orders)


def test_empty_queue_rejects_an_order_from_the_middle_of_another_queue() -> None:
    """O caso que a guarda por ponteiros deixava passar, e com o pior desfecho.

    A remoção seguia adiante, desligava a ordem da fila alheia e decrementava o contador
    desta, deixando ``_size`` negativo e ``is_empty`` respondendo ``False`` para uma fila
    vazia — nível fantasma no livro, porque é ``is_empty`` que decide se o nível de preço
    sai do índice.
    """
    queue = OrderQueue()
    other, other_orders = make_queue(3)

    with pytest.raises(QueueIntegrityError, match="não pertence"):
        queue.remove(other_orders[1])

    assert_queue_is(queue, [])
    assert_queue_is(other, other_orders)


def test_remove_rejects_an_order_already_removed() -> None:
    queue, orders = make_queue(3)
    queue.remove(orders[1])

    with pytest.raises(QueueIntegrityError, match="não pertence"):
        queue.remove(orders[1])

    assert_queue_is(queue, [orders[0], orders[2]])


def test_order_records_the_queue_it_belongs_to() -> None:
    """A alça é o que sustenta as duas guardas, e volta ao ponto de partida na saída."""
    queue = OrderQueue()
    order = make_order(1)
    assert order.queue is None

    queue.append(order)
    assert order.queue is queue

    queue.remove(order)
    assert order.queue is None


def test_queue_integrity_error_is_a_runtime_error() -> None:
    """Violação aqui é bug da engine, não entrada inválida do usuário."""
    assert issubclass(QueueIntegrityError, RuntimeError)
    assert not issubclass(QueueIntegrityError, ValueError)


def test_long_queue_keeps_order_and_size() -> None:
    queue, orders = make_queue(1000)

    assert_queue_is(queue, orders)


def test_long_queue_survives_removals_from_the_middle() -> None:
    """Remover metade das ordens espalhadas pela fila não embaralha as que ficam."""
    queue, orders = make_queue(1000)

    for order in orders[::2]:
        queue.remove(order)

    assert_queue_is(queue, orders[1::2])
    assert len(queue) == 500
