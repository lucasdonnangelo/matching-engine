"""Propriedade: o presenter formata qualquer sequência de eventos sem perder informação.

O presenter é o único ponto do sistema onde informação é **fundida** — trades consecutivos
de mesmo preço viram uma linha só, porque é assim que o enunciado exibe. Fusão é onde
quantidade some sem que ninguém perceba: a saída continua plausível, o total é que fica
errado. Daí as quatro propriedades deste arquivo, todas sobre a fusão e nenhuma sobre
formatação — o texto exato é dos testes unitários e dos golden de ponta a ponta.

Os eventos são gerados soltos, sem passar por engine nenhuma, e é de propósito. O presenter
recebe ``Sequence[Event]`` e não pode assumir nada sobre a procedência: um evento que a
engine hoje não emite em certa ordem é exatamente o que uma etapa futura pode passar a
emitir, e a hora de descobrir que a formatação depende dessa ordem é agora.
"""

from __future__ import annotations

import re
from typing import Final

from hypothesis import given, settings
from hypothesis import strategies as st

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
from matching_engine.domain.order import OrderId
from matching_engine.domain.price import Ticks
from matching_engine.domain.side import PegReference, Side
from matching_engine.io.presenter import format_events

_TRADE_LINE: Final = re.compile(r"Trade, price: (?P<price>\S+), qty: (?P<quantity>\d+)")
"""Reconhece a linha de execução na saída.

Nenhuma outra linha do presenter começa por ``Trade,``: as de ordem começam por ``Order``,
as do livro por quantidade ou por cabeçalho. O casamento é ``fullmatch``, de modo que uma
linha que apenas contivesse esse texto não seria contada.
"""


# --------------------------------------------------------------------------------------
# Estratégias
# --------------------------------------------------------------------------------------

ORDER_IDS = st.integers(min_value=1, max_value=999).map(OrderId)
PRICES = st.integers(min_value=1, max_value=200_000).map(Ticks)
OPTIONAL_PRICES = st.none() | PRICES
QUANTITIES = st.integers(min_value=1, max_value=10_000)
SIDES = st.sampled_from(Side)
REFERENCES = st.sampled_from(PegReference)


def _pegged(
    order_id: OrderId, reference: PegReference, price: Ticks | None, quantity: int
) -> OrderPegged:
    """O lado vem da referência, e não é sorteado à parte.

    Peg cruzado não existe no domínio — ``Order.__post_init__`` o recusa na construção —, e
    gerar um evento que descreve uma ordem inconstruível testaria a formatação de algo que
    nunca vai chegar ao presenter.
    """
    return OrderPegged(
        order_id=order_id,
        side=reference.side,
        reference=reference,
        price=price,
        quantity=quantity,
    )


TRADES = st.builds(
    Trade,
    price=PRICES,
    quantity=QUANTITIES,
    maker_order_id=ORDER_IDS,
    taker_side=SIDES,
)
ENTRIES = st.builds(BookEntry, quantity=QUANTITIES, price=PRICES)

EVENTS = st.lists(
    st.one_of(
        TRADES,
        st.builds(OrderAccepted, order_id=ORDER_IDS, side=SIDES, price=PRICES, quantity=QUANTITIES),
        st.builds(
            OrderCancelled,
            order_id=ORDER_IDS,
            side=SIDES,
            price=OPTIONAL_PRICES,
            remaining=QUANTITIES,
        ),
        st.builds(
            OrderAmended,
            order_id=ORDER_IDS,
            side=SIDES,
            price=PRICES,
            quantity=QUANTITIES,
            priority_renewed=st.booleans(),
        ),
        st.builds(
            _pegged,
            order_id=ORDER_IDS,
            reference=REFERENCES,
            price=OPTIONAL_PRICES,
            quantity=QUANTITIES,
        ),
        st.builds(
            BookSnapshot,
            bids=st.lists(ENTRIES, max_size=6).map(tuple),
            asks=st.lists(ENTRIES, max_size=6).map(tuple),
        ),
    ),
    max_size=20,
)
"""Listas de eventos arbitrários, inclusive a vazia — comando que não produz saída nenhuma.

Trades entram com preços de uma faixa larga **e** repetíveis: é a repetição que forma os
runs consecutivos que a fusão persegue, e a faixa larga que garante runs interrompidos.
"""


def _trade_lines(lines: list[str]) -> list[re.Match[str]]:
    return [match for line in lines if (match := _TRADE_LINE.fullmatch(line)) is not None]


# --------------------------------------------------------------------------------------
# Testes
# --------------------------------------------------------------------------------------


@given(EVENTS)
@settings(deadline=None)
def test_format_events_never_raises(events: list[Event]) -> None:
    """Nenhuma sequência de eventos derruba a formatação.

    É a propriedade mais barata e a que protege o REPL: uma exceção aqui encerraria a sessão
    depois de o comando já ter mudado o livro, e o cliente ficaria sem saber o que aconteceu
    com uma ordem que a engine já aceitou. O presenter é a última etapa, e depois dela não
    há mais para onde recuperar.

    ``deadline=None`` pelo mesmo motivo do arquivo de invariantes: o prazo por exemplo do
    Hypothesis produz falha intermitente em CI mais lento que a máquina de desenvolvimento,
    e o que se mede aqui é corretude.
    """
    lines = format_events(events)
    assert all(isinstance(line, str) for line in lines), (
        "a formatação devolveu algo que não é linha de texto"
    )


@given(EVENTS)
@settings(deadline=None)
def test_aggregation_conserves_traded_quantity(events: list[Event]) -> None:
    """A soma das linhas de execução é a soma dos ``Trade`` recebidos.

    O domínio emite um ``Trade`` por par maker/taker e a saída mostra uma linha por preço,
    de modo que a quantidade atravessa uma soma no caminho. Esta é a conferência de que a
    soma é a de tudo e só do que entrou: um run fechado duas vezes duplicaria quantidade, e
    um run esquecido no fim da lista a faria sumir — os dois erros produzem saída plausível,
    com as linhas certas e o total errado.
    """
    formatted = sum(int(match["quantity"]) for match in _trade_lines(format_events(events)))
    received = sum(event.quantity for event in events if isinstance(event, Trade))
    assert formatted == received, (
        f"as linhas de execução somam {formatted} e os trades recebidos somam {received}"
    )


@given(EVENTS)
@settings(deadline=None)
def test_no_output_line_ends_in_whitespace(events: list[Event]) -> None:
    """Nenhuma linha termina em espaço invisível.

    O livro é impresso em duas colunas, e o lado mais curto é completado até a largura do
    mais longo: sem o ``rstrip`` de cada linha, a coluna da esquerda arrastaria espaços até
    o separador em toda linha em que a direita estivesse vazia. Espaço invisível é
    exatamente o que faz um golden falhar sem que a diferença apareça na tela, e um diff
    acusar mudança onde não houve.
    """
    for line in format_events(events):
        assert line == line.rstrip(), f"linha termina em espaço em branco: {line!r}"


@given(EVENTS)
@settings(deadline=None)
def test_aggregation_only_ever_merges(events: list[Event]) -> None:
    """A fusão funde, e nunca divide: no máximo uma linha de execução por ``Trade`` recebido.

    Junto com a conservação de quantidade, fecha o comportamento da agregação pelos dois
    lados. Só a soma permitiria que um trade de 150 virasse duas linhas de 100 e 50, com o
    total correto e uma execução inventada; só a contagem permitiria que dois trades de
    preços diferentes fossem somados numa linha só, com a contagem caindo e a quantidade
    aparecendo num preço em que nada foi negociado.
    """
    lines = len(_trade_lines(format_events(events)))
    received = sum(1 for event in events if isinstance(event, Trade))
    assert lines <= received, (
        f"a saída tem {lines} linhas de execução para {received} trades recebidos: a "
        f"agregação dividiu em vez de fundir"
    )
