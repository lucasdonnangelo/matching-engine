from collections.abc import Callable
from functools import partial

import pytest

from matching_engine.domain.order import (
    InvalidOrderError,
    Order,
    OrderId,
    OrderIntegrityError,
)
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


def test_order_integrity_error_is_a_runtime_error() -> None:
    """Over-fill e alteração de ordem enfileirada são bug da engine, não erro de digitação.

    Fosse ``ValueError``, o ``Cli`` os converteria em linha ``Error:`` e seguiria a sessão
    sobre um livro cujas contas já não fecham.
    """
    assert issubclass(OrderIntegrityError, RuntimeError)
    assert not issubclass(OrderIntegrityError, ValueError)


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
    """Nenhum dos dois casos vem do teclado: o matching executa ``min`` dos remanescentes."""
    order = make_order(quantity=10)

    with pytest.raises(OrderIntegrityError, match=message):
        order.fill(qty)

    assert order.remaining == 10  # a execução recusada não deixa rastro


def test_fill_beyond_the_remaining_is_rejected_even_within_the_quantity() -> None:
    """Executar duas vezes 6 de uma ordem de 10 é erro de contabilidade, não arredondamento."""
    order = make_order(quantity=10)
    order.fill(6)

    with pytest.raises(OrderIntegrityError, match="excede o remanescente"):
        order.fill(6)

    assert order.remaining == 4


def test_reduce_to_shrinks_the_quantity_together_with_the_balance() -> None:
    """Depois de reduzir, a ordem **é** de outro tamanho; é contra ele que o amend compara."""
    order = make_order(quantity=100)

    order.reduce_to(80)

    assert order.quantity == 80
    assert order.remaining == 80


def test_reduce_to_preserves_what_was_already_executed() -> None:
    order = make_order(quantity=100)
    order.fill(40)

    order.reduce_to(50)

    assert order.quantity == 90  # 50 por executar mais os 40 que já executaram
    assert order.remaining == 50
    assert order.quantity - order.remaining == 40


def test_shrinking_and_growing_back_is_an_increase() -> None:
    """Se a quantidade não acompanhasse, encolher e voltar seria crescer de graça."""
    order = make_order(quantity=100)

    order.reduce_to(80)

    assert order.quantity == 80  # 90 depois disto seria aumento, e pagaria prioridade


@pytest.mark.parametrize(
    ("new_remaining", "message"),
    [
        (0, "saldo positivo"),
        (-1, "saldo positivo"),
        (10, "não encolhe"),
        (11, "não encolhe"),
    ],
)
def test_reduce_to_rejects(new_remaining: int, message: str) -> None:
    """Quem chama é ``PriceLevel.reduce``, código da engine: faixa errada aqui é bug dela.

    O número que o cliente digita é validado em ``MatchingEngine.amend``, camadas acima.
    """
    order = make_order(quantity=10)

    with pytest.raises(OrderIntegrityError, match=message):
        order.reduce_to(new_remaining)

    assert order.quantity == 10  # a redução recusada não deixa rastro
    assert order.remaining == 10


def test_amend_to_rewrites_the_price_and_the_size_together() -> None:
    order = make_order(quantity=100)

    order.amend_to(Ticks(998), 80)

    assert order.price == 998
    assert order.quantity == 80
    assert order.remaining == 80


def test_amend_to_preserves_what_was_already_executed() -> None:
    """Ordem de 100 com 40 executados, alterada para 80: sobram 40 por executar."""
    order = make_order(quantity=100)
    order.fill(40)

    order.amend_to(Ticks(998), 80)

    assert order.quantity == 80
    assert order.remaining == 40
    assert order.quantity - order.remaining == 40  # alterar não desfaz negócio


@pytest.mark.parametrize("new_quantity", [40, 30, 0, -5])
def test_amend_to_below_what_was_executed_is_rejected(new_quantity: int) -> None:
    """Aqui é o cliente pedindo o impossível, e por isso ``ValueError``: os 40 não voltam."""
    order = make_order(quantity=100)
    order.fill(40)

    with pytest.raises(InvalidOrderError, match="já executou"):
        order.amend_to(Ticks(998), new_quantity)

    assert order.price == SOME_PRICE  # a alteração recusada não deixa rastro
    assert order.quantity == 100
    assert order.remaining == 60


def test_renew_priority_changes_only_the_place_in_the_queue() -> None:
    order = make_order(quantity=10)
    order.fill(4)

    order.renew_priority(99)

    assert order.sequence_id == 99
    assert order.order_id == 1  # a identidade que o cliente conhece não muda
    assert order.quantity == 10
    assert order.remaining == 6
    assert order.price == SOME_PRICE


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
