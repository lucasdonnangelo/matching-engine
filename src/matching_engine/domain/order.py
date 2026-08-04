"""Ordem: o dado que o livro guarda e a engine executa.

Toda combinação impossível é barrada na construção, não no matching: ``peg bid sell`` e
limit sem preço não existem como objeto. Quem recebe um ``Order`` adiante — nível de
preço, livro, engine — não precisa reverificar nada disso.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NewType

from matching_engine.domain.price import Ticks
from matching_engine.domain.side import PegReference, Side

OrderId = NewType("OrderId", int)


class InvalidOrderError(ValueError):
    """Ordem construída fora dos seus invariantes, ou execução inválida sobre ela."""


@dataclass(slots=True, eq=False)
class Order:
    """Uma ordem viva, mutável enquanto tiver remanescente.

    ``order_id`` e ``sequence_id`` são campos separados porque respondem a perguntas
    diferentes: o primeiro é *quem* a ordem é, o segundo é *onde* ela está na fila.
    Nascem juntos, mas divergem no amend — alterar preço ou aumentar quantidade custa
    prioridade, então a ordem recebe um novo ``sequence_id`` e vai para o fim da fila,
    enquanto o ``order_id`` permanece, porque é por ele que o cliente segue chamando
    ``cancel`` e ``amend``. Com um campo só, ou o amend quebraria a referência do
    cliente, ou a ordem alterada manteria uma prioridade que já não merece. A
    reprecificação de uma pegged faz o inverso: preserva o ``sequence_id``, porque a
    mudança partiu da engine e não do cliente.

    ``eq=False`` preserva a igualdade por identidade herdada de ``object``, porque a
    ordem é entidade e não value object: duas ordens de campos idênticos são ordens
    diferentes, e a mesma ordem depois de um ``fill`` continua sendo ela. Igualdade
    estrutural inverteria as duas coisas — e com elas todo ``in`` e ``.remove()``, que
    se apoiam em ``__eq__``. Custaria também o hash, já que com ``eq=True`` e
    ``frozen=False`` o dataclass fixa ``__hash__ = None`` e a ordem não poderia entrar
    em ``set`` nem ser chave de ``dict``. E, com os ponteiros de fila intrusiva que
    vêm a seguir, comparar duas ordens percorreria a lista encadeada até o
    ``RecursionError``.

    ``price`` é opcional porque uma ordem pegged sem referência não tem preço nenhum a
    exibir: ela fica *parked* fora do livro até que surja uma ordem não-pegged do seu
    lado, e só então recebe preço e entra na fila. Descartá-la seria perder a intenção
    do cliente; inventar um preço seria mentir sobre o livro. ``None`` é exclusivo
    desse estado — ordem não-pegged sempre tem preço.

    ``remaining`` não é parâmetro do construtor: nasce igual a ``quantity`` e daí em
    diante só encolhe por ``fill``. Nenhum caminho legítimo cria uma ordem já
    parcialmente executada — o amend muta a ordem existente em vez de construir outra —,
    então o estado inconsistente não é validado, é inconstruível.

    ``quantity`` sobrevive à execução parcial porque é ele, e não o saldo por executar,
    a base de comparação da política de amend: aumentar quantidade custa prioridade,
    reduzir a mantém, e a convenção compara contra a quantidade da ordem. Para uma ordem
    parcialmente executada as duas bases divergem — pedir 8 numa ordem de 10 com 4 já
    executados é redução perante a quantidade e aumento perante o remanescente —, de
    modo que sem o valor original a regra ficaria indecidível justamente onde mais
    importa.
    """

    order_id: OrderId
    sequence_id: int
    side: Side
    price: Ticks | None
    quantity: int
    remaining: int = field(init=False)
    peg_reference: PegReference | None = None

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise InvalidOrderError(f"quantidade deve ser maior que zero: {self.quantity}")
        self.remaining = self.quantity
        if self.peg_reference is None:
            if self.price is None:
                raise InvalidOrderError("ordem sem preço só é válida se for pegged")
        elif self.peg_reference.side is not self.side:
            raise InvalidOrderError(
                f"peg cruzado: referência {self.peg_reference.name} não acompanha o lado "
                f"{self.side.name}"
            )

    @property
    def is_filled(self) -> bool:
        """Ordem totalmente executada; sai do livro."""
        return self.remaining == 0

    @property
    def is_pegged(self) -> bool:
        """Ordem cujo preço é ditado pelo topo do livro, não pelo cliente."""
        return self.peg_reference is not None

    @property
    def is_parked(self) -> bool:
        """Pegged à espera de referência: viva, mas fora do livro e sem preço."""
        return self.is_pegged and self.price is None

    def fill(self, qty: int) -> None:
        """Consome parte do remanescente.

        A recusa de ``qty`` maior que o remanescente é guarda contra inconsistência
        interna, não validação de entrada do cliente: o matching sempre executa
        ``min(remaining, incoming_remaining)``, então um over-fill não chega aqui vindo
        do usuário — só pode ser bug da engine. Falhar alto no ponto do erro evita que
        um remanescente negativo se propague em silêncio pelos totais do nível de preço.
        """
        if qty <= 0:
            raise InvalidOrderError(f"execução deve ter quantidade positiva: {qty}")
        if qty > self.remaining:
            raise InvalidOrderError(f"execução de {qty} excede o remanescente de {self.remaining}")
        self.remaining -= qty
