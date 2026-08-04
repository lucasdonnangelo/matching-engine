"""``Event`` -> texto. Funções puras: nada aqui imprime, nada aqui decide.

Este módulo existe para que a agregação por preço aconteça **fora** do domínio. A engine
emite um ``Trade`` por par maker/taker porque precisa de granularidade auditável — qual
ordem passiva foi atingida, a que preço, em que quantidade —, enquanto o enunciado mostra
uma linha por preço. Os dois estão certos, cada um no seu lugar.

O exemplo do enunciado é literalmente esse caso: com 100 @ 20 e 200 @ 20 no livro, um
``market buy 150`` atinge duas ordens passivas e produz dois ``Trade`` — 100 e 50 —, que
saem como uma única linha ``Trade, price: 20, qty: 150``.

A direção da conversão é o argumento inteiro. Fundir no domínio destruiria informação que
não se recupera: dos dois trades chega-se à linha somada, da linha somada não se volta aos
dois trades, e a identidade das ordens passivas atingidas se perderia para sempre — junto
com a possibilidade de auditar uma execução. Fundir aqui é uma projeção, refeita a cada
apresentação, sobre dados que continuam íntegros do outro lado da fronteira. E, como é
projeção, trocar a formatação não toca em uma linha do núcleo.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import assert_never

from matching_engine.domain.events import Event, OrderAccepted, Trade
from matching_engine.domain.price import Ticks, format_price


def format_events(events: Sequence[Event]) -> list[str]:
    """Formata os eventos de um comando, fundindo trades **consecutivos** de mesmo preço.

    Consecutivos, e não todos os de mesmo preço: se o preço varia e volta — A, B, A —, as
    três linhas ficam, porque a cronologia é informação. Um agressor que executou a 20,
    depois a 21, depois a 20 de novo atravessou o livro de um jeito que a soma por preço
    esconderia; a saída ficaria com duas linhas sugerindo dois níveis varridos em ordem,
    quando foram três passagens. Agrupar por chave é reordenar, e reordenar execução é
    mentir sobre o que aconteceu.

    A fusão também não atravessa um evento de outro tipo, pelo mesmo motivo: entre dois
    trades de preço igual pode haver uma ordem aceita, e a ordem dos acontecimentos é o
    que a saída relata.
    """
    lines: list[str] = []
    pending_price: Ticks | None = None
    pending_quantity = 0

    for event in events:
        match event:
            case Trade():
                if pending_price is not None and pending_price != event.price:
                    lines.append(_trade_line(pending_price, pending_quantity))
                    pending_quantity = 0
                pending_price = event.price
                pending_quantity += event.quantity
            case OrderAccepted():
                if pending_price is not None:
                    lines.append(_trade_line(pending_price, pending_quantity))
                    pending_price, pending_quantity = None, 0
                lines.append(_accepted_line(event))
            case _:
                assert_never(event)

    if pending_price is not None:
        lines.append(_trade_line(pending_price, pending_quantity))
    return lines


def _trade_line(price: Ticks, quantity: int) -> str:
    """Recebe preço e quantidade soltos, e não um ``Trade``, porque a quantidade é somada.

    Um ``Trade`` sintético com a quantidade agregada teria de carregar o
    ``maker_order_id`` de uma das ordens atingidas, e o evento passaria a afirmar algo
    falso sobre quem executou.
    """
    return f"Trade, price: {format_price(price)}, qty: {quantity}"


def _accepted_line(event: OrderAccepted) -> str:
    """Lado em minúsculas, como o usuário digitou; o ``Enum`` é do domínio, não da saída."""
    return (
        f"Order created: {event.side.name.lower()} {event.quantity} "
        f"@ {format_price(event.price)} {event.order_id}"
    )
