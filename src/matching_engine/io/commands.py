"""Comandos: a intenção do usuário depois do parsing e antes da execução.

O parser devolve um comando; o ``Cli`` o executa. O passo intermediário poderia não
existir — ``parse`` chamaria a engine direto —, e é justamente por isso que ele existe: o
parser se testa comparando dataclasses, sem engine, sem livro e sem estado; e o despacho
se testa sem passar por texto nenhum. Fundidos, cada teste de gramática arrastaria junto
uma execução, e um erro de matching apareceria como falha de parsing.

Estes tipos moram em ``io/`` e não em ``domain/`` porque descrevem o **protocolo de
texto**, não o negócio. ``PrintBook`` e ``Quit`` não são conceitos de um livro de ofertas;
são coisas que uma sessão de terminal precisa dizer. E ``ModifyOrder`` carrega dois campos
opcionais porque a sintaxe ``modify order <id> [price <p>] [qty <q>]`` admite omitir um
dos dois — a forma do comando é a forma da linha digitada, e o domínio não deve conhecê-la.

Os comandos são imutáveis: uma vez lida, a intenção do usuário não é reescrita no caminho
até a engine.
"""

from __future__ import annotations

from dataclasses import dataclass

from matching_engine.domain.order import OrderId
from matching_engine.domain.price import Ticks
from matching_engine.domain.side import PegReference, Side


@dataclass(frozen=True, slots=True)
class SubmitLimit:
    """``limit <buy|sell> <preço> <quantidade>``."""

    side: Side
    price: Ticks
    quantity: int


@dataclass(frozen=True, slots=True)
class SubmitMarket:
    """``market <buy|sell> <quantidade>``; não tem preço, e é isso que a define."""

    side: Side
    quantity: int


@dataclass(frozen=True, slots=True)
class SubmitPegged:
    """``peg <bid|offer> <buy|sell> <quantidade>``.

    Referência e lado vêm separados porque a linha os traz separados. Que ``peg bid sell``
    seja inválido é regra de negócio, e quem a aplica é ``Order``, no domínio: repeti-la
    aqui criaria duas fontes para a mesma decisão, que divergiriam na primeira mudança.
    """

    reference: PegReference
    side: Side
    quantity: int


@dataclass(frozen=True, slots=True)
class CancelOrder:
    """``cancel order <id>``."""

    order_id: OrderId


@dataclass(frozen=True, slots=True)
class ModifyOrder:
    """``modify order <id> [price <preço>] [qty <quantidade>]``.

    ``None`` significa "não mexer neste campo", e não "apagar o valor". A gramática exige
    ao menos um dos dois preenchidos, então os dois ``None`` ao mesmo tempo não são um
    comando construível pelo parser.
    """

    order_id: OrderId
    price: Ticks | None
    quantity: int | None


@dataclass(frozen=True, slots=True)
class PrintBook:
    """``print book``."""


@dataclass(frozen=True, slots=True)
class Quit:
    """``quit`` ou ``exit``: encerra a sessão, sem tocar no livro."""


type Command = (
    SubmitLimit | SubmitMarket | SubmitPegged | CancelOrder | ModifyOrder | PrintBook | Quit
)
"""Tudo que uma linha válida pode significar.

União de tipos pelo mesmo motivo de ``Event``: o despacho do ``Cli`` usa ``match``/``case``
com ``assert_never`` no ramo final, então acrescentar um comando sem tratá-lo passa a ser
erro de ``mypy``, e não comando que o REPL ignora em silêncio.
"""
