from collections.abc import Callable
from functools import partial

import pytest

from matching_engine.domain.order import InvalidOrderError, Order, OrderId
from matching_engine.domain.price import Ticks
from matching_engine.domain.side import PegReference, Side

SOME_PRICE = Ticks(1000)


def make_order(
    *,
    side: Side = Side.BUY,
    price: Ticks | None = SOME_PRICE,
    quantity: int = 10,
    peg_reference: PegReference | None = None,
) -> Order:
    """Ordem limit de compra por padrão; cada teste sobrescreve só o que examina."""
    return Order(
        order_id=OrderId(1),
        sequence_id=1,
        side=side,
        price=price,
        quantity=quantity,
        peg_reference=peg_reference,
    )


@pytest.mark.parametrize("side", list(Side))
def test_limit_order_is_valid(side: Side) -> None:
    order = make_order(side=side, price=Ticks(1050), quantity=7)

    assert order.side is side
    assert order.price == 1050
    assert order.quantity == 7
    assert order.remaining == 7
    assert not order.is_filled
    assert not order.is_pegged
    assert not order.is_parked


@pytest.mark.parametrize(
    ("peg_reference", "side"),
    [(PegReference.BID, Side.BUY), (PegReference.OFFER, Side.SELL)],
)
def test_homolateral_peg_is_valid(peg_reference: PegReference, side: Side) -> None:
    order = make_order(side=side, peg_reference=peg_reference)

    assert order.peg_reference is peg_reference
    assert order.is_pegged


@pytest.mark.parametrize(
    ("peg_reference", "side"),
    [(PegReference.BID, Side.BUY), (PegReference.OFFER, Side.SELL)],
)
def test_parked_peg_is_valid(peg_reference: PegReference, side: Side) -> None:
    """Sem referência no livro a pegged fica sem preço, e isso é um estado legítimo."""
    order = make_order(side=side, price=None, peg_reference=peg_reference)

    assert order.price is None
    assert order.is_parked


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (partial(make_order, quantity=0), "quantidade deve ser maior que zero"),
        (partial(make_order, quantity=-5), "quantidade deve ser maior que zero"),
        (partial(make_order, price=None), "sem preço só é válida se for pegged"),
        (
            partial(make_order, side=Side.SELL, peg_reference=PegReference.BID),
            "peg cruzado",
        ),
        (
            partial(make_order, side=Side.BUY, peg_reference=PegReference.OFFER),
            "peg cruzado",
        ),
    ],
    ids=[
        "quantidade zero",
        "quantidade negativa",
        "limit sem preço",
        "peg bid em ordem de venda",
        "peg offer em ordem de compra",
    ],
)
def test_construction_rejects(build: Callable[[], Order], message: str) -> None:
    with pytest.raises(InvalidOrderError, match=message):
        build()


def test_invalid_order_is_a_value_error() -> None:
    assert issubclass(InvalidOrderError, ValueError)


def test_new_order_has_the_whole_quantity_remaining() -> None:
    """Ordem parcialmente executada não se constrói: só se chega nela por ``fill``."""
    order = make_order(quantity=7)

    assert order.remaining == 7
    assert order.remaining == order.quantity


def test_orders_with_identical_fields_are_not_equal() -> None:
    """Ordem é entidade: quem a identifica é o objeto, não a combinação de campos."""
    assert make_order() != make_order()


def test_order_is_hashable() -> None:
    """Livro e índices guardam ordens em ``set`` e ``dict``."""
    order = make_order()

    assert order in {order}


def test_partial_fill_reduces_remaining() -> None:
    order = make_order(quantity=10)

    order.fill(4)

    assert order.remaining == 6
    assert order.quantity == 10  # a quantidade original não muda com a execução
    assert not order.is_filled


def test_full_fill_marks_the_order_as_filled() -> None:
    order = make_order(quantity=10)

    order.fill(10)

    assert order.remaining == 0
    assert order.is_filled


def test_successive_fills_exhaust_the_order() -> None:
    order = make_order(quantity=10)

    order.fill(3)
    order.fill(3)
    order.fill(4)

    assert order.remaining == 0
    assert order.is_filled


@pytest.mark.parametrize(
    ("qty", "message"),
    [
        (0, "quantidade positiva"),
        (-1, "quantidade positiva"),
        (11, "excede o remanescente"),
    ],
)
def test_fill_rejects(qty: int, message: str) -> None:
    order = make_order(quantity=10)

    with pytest.raises(InvalidOrderError, match=message):
        order.fill(qty)

    assert order.remaining == 10  # a execução recusada não deixa rastro


def test_fill_beyond_the_remaining_is_rejected_even_within_the_quantity() -> None:
    """Executar duas vezes 6 de uma ordem de 10 é erro de contabilidade, não arredondamento."""
    order = make_order(quantity=10)
    order.fill(6)

    with pytest.raises(InvalidOrderError, match="excede o remanescente"):
        order.fill(6)

    assert order.remaining == 4


@pytest.mark.parametrize(
    ("price", "peg_reference", "pegged", "parked"),
    [
        (SOME_PRICE, None, False, False),
        (SOME_PRICE, PegReference.BID, True, False),
        (None, PegReference.BID, True, True),
    ],
    ids=["limit com preço", "pegged com preço", "pegged sem preço"],
)
def test_pegged_and_parked(
    price: Ticks | None, peg_reference: PegReference | None, pegged: bool, parked: bool
) -> None:
    """A quarta combinação — sem peg e sem preço — não existe: é rejeitada na construção."""
    order = make_order(side=Side.BUY, price=price, peg_reference=peg_reference)

    assert order.is_pegged is pegged
    assert order.is_parked is parked
