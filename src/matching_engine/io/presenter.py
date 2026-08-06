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
from typing import Final, assert_never

from matching_engine.domain.events import (
    BookEntry,
    BookSnapshot,
    Event,
    OrderAccepted,
    OrderAmended,
    OrderCancelled,
    Trade,
)
from matching_engine.domain.order import OrderId
from matching_engine.domain.price import Ticks, format_price
from matching_engine.domain.side import Side

_BIDS_HEADER: Final = "Ordens de Compra"
_ASKS_HEADER: Final = "Ordens de Venda"

_CANCELLED_LINE: Final = "Order cancelled"
"""Linha inteira, fixada pelo enunciado: sem id, sem quantidade, sem lado.

O evento carrega os três, e a escolha de não mostrá-los é do presenter — que é onde as
decisões de apresentação são reversíveis.
"""


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

    Esse fechamento é um guard **único**, antes do ``match``, e não uma linha repetida em
    cada ramo. A repetição funcionava, mas deixava a corretude por conta da memória de quem
    escrevesse o próximo ramo: ``assert_never`` obriga a tratar todo evento novo, e não tem
    como obrigar a fechar o run pendente dentro do ramo tratado. O esquecimento não
    quebraria a build nem estouraria em teste algum — produziria uma linha de trade fora da
    ordem cronológica, saída plausível e errada, que é a espécie mais cara de diagnosticar.
    Com o guard único, a regra "todo evento que não é ``Trade`` fecha o run" vale para o
    evento novo por construção, e o ramo cuida só da própria formatação.
    """
    lines: list[str] = []
    pending_price: Ticks | None = None
    pending_quantity = 0

    for event in events:
        if not isinstance(event, Trade) and pending_price is not None:
            lines.append(_trade_line(pending_price, pending_quantity))
            pending_price, pending_quantity = None, 0

        match event:
            case Trade():
                if pending_price is not None and pending_price != event.price:
                    lines.append(_trade_line(pending_price, pending_quantity))
                    pending_quantity = 0
                pending_price = event.price
                pending_quantity += event.quantity
            case OrderAccepted():
                lines.append(_accepted_line(event))
            case OrderAmended():
                lines.append(_amended_line(event))
            case OrderCancelled():
                lines.append(_CANCELLED_LINE)
            case BookSnapshot():
                lines.extend(_book_lines(event))
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


def _book_lines(snapshot: BookSnapshot) -> list[str]:
    """As duas colunas do livro, lado a lado, com o cabeçalho e o separador.

    A largura de cada coluna é medida a partir do próprio conteúdo — o maior entre o
    cabeçalho e a maior linha daquele lado —, e não fixada num número. Preço tem largura
    variável (``10``, ``9.99``, ``10.5``) e quantidade também, de modo que qualquer largura
    constante ou estouraria a coluna no primeiro preço longo, ou deixaria um vão permanente.

    Os dois lados quase nunca têm o mesmo número de ordens, então o mais curto é completado
    com string vazia até o comprimento do mais longo: a tabela é retangular mesmo quando o
    livro não é.

    Cada linha termina com ``rstrip``. Sem isso, a linha de um lado só carregaria espaços
    invisíveis até a coluna da direita, e espaço invisível é o que faz um teste golden
    falhar sem que a diferença apareça na tela, e um diff acusar mudança onde não houve.
    """
    bids = [_entry_line(entry) for entry in snapshot.bids]
    asks = [_entry_line(entry) for entry in snapshot.asks]
    bids_width = max([len(_BIDS_HEADER), *(len(line) for line in bids)])
    asks_width = max([len(_ASKS_HEADER), *(len(line) for line in asks)])

    lines = [
        _book_row(_BIDS_HEADER, _ASKS_HEADER, bids_width),
        f"{'-' * bids_width}-|-{'-' * asks_width}",
    ]
    for index in range(max(len(bids), len(asks))):
        bid = bids[index] if index < len(bids) else ""
        ask = asks[index] if index < len(asks) else ""
        lines.append(_book_row(bid, ask, bids_width))
    return lines


def _entry_line(entry: BookEntry) -> str:
    return f"{entry.quantity} @ {format_price(entry.price)}"


def _book_row(left: str, right: str, left_width: int) -> str:
    return f"{left.ljust(left_width)} | {right}".rstrip()


def _accepted_line(event: OrderAccepted) -> str:
    return _order_line("created", event.side, event.quantity, event.price, event.order_id)


def _amended_line(event: OrderAmended) -> str:
    """Mesma linha da ordem criada, com o verbo trocado.

    O enunciado **não** especifica saída para alteração de ordem: ele mostra o livro depois
    do amend, e não a linha de confirmação. Silêncio não serve, porque num REPL o comando
    que não responde é indistinguível do comando que não fez nada. Entre inventar um
    formato e reaproveitar o que já existe, reaproveitar: quem leu ``Order created: buy 200
    @ 10 1`` lê ``Order amended: buy 200 @ 9.98 1`` sem aprender nada novo, e o id repetido
    no fim deixa visível que a ordem continua sendo a mesma — que é justamente o que
    distingue um amend de um cancelamento seguido de ordem nova.

    ``priority_renewed`` fica de fora da linha. O efeito dele é a posição da ordem na fila,
    e a fila é o que ``print book`` mostra: anunciá-lo aqui seria descrever em palavras o
    que o comando seguinte exibe em ordem.
    """
    return _order_line("amended", event.side, event.quantity, event.price, event.order_id)


def _order_line(verb: str, side: Side, quantity: int, price: Ticks, order_id: OrderId) -> str:
    """Forma comum às duas linhas de ordem, para que espelhar não dependa de memória.

    Lado em minúsculas, como o usuário digitou; o ``Enum`` é do domínio, não da saída.
    """
    return f"Order {verb}: {side.name.lower()} {quantity} @ {format_price(price)} {order_id}"
