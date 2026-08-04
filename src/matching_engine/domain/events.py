"""Eventos de domínio: o resultado de um comando, antes de virar texto.

A engine devolve ``list[Event]`` e não imprime coisa alguma. Quem transforma evento em
linha na tela é ``io/presenter.py``, do outro lado da fronteira da seção 2 do contrato.
São dois ganhos concretos: o domínio se testa comparando dataclasses — sem capturar
stdout, sem depender de espaçamento nem da ordem em que as linhas saem — e a CLI pode ser
trocada por outra interface sem que uma linha do núcleo mude.

Todo evento é um retrato imutável de dados escalares, e nenhum deles guarda referência a
uma ``Order`` viva. Essa é a decisão central do módulo: a ordem continua mudando depois
do evento — executa mais, é alterada, é cancelada —, de modo que um evento que a
apontasse descreveria, no momento em que fosse lido, um estado que não é o do instante
que ele relata. Copiar os poucos campos que importam custa alguns inteiros e faz do
evento um fato consumado, em vez de uma janela para estado que se move.

O domínio emite **um** ``Trade`` por par maker/taker, e não um agregado por preço. A
granularidade fina é a auditável: qual ordem passiva foi atingida, a que preço e em que
quantidade. O enunciado agrega por preço na saída, mas agregar é projeção — sai-se do
detalhe para o total, e do total não se volta. A soma pertence ao presenter, que é onde a
decisão de apresentação mora.

``Event`` é união de tipos, e não hierarquia de classes. O presenter trata os eventos com
``match``/``case`` e fecha o último ramo com ``assert_never``: acrescentar um evento novo
sem tratá-lo vira erro de ``mypy``, e não surpresa em tempo de execução. É exaustividade
verificada estaticamente, sem classe base, sem método abstrato e sem nada a reimplementar
a cada evento — o que também mantém os eventos como o que eles são, dados, e não objetos
com comportamento.
"""

from __future__ import annotations

from dataclasses import dataclass

from matching_engine.domain.order import OrderId
from matching_engine.domain.price import Ticks
from matching_engine.domain.side import Side


@dataclass(frozen=True, slots=True)
class Trade:
    """Uma execução entre a ordem agressora e **uma** ordem passiva.

    ``price`` é sempre o preço do maker, nunca o do agressor; o porquê está em
    ``MatchingEngine._submit`` e na ADR 0001.

    Não existe ``taker_order_id`` porque nem sempre existe um id a registrar: uma market
    nunca chega a ser uma ``Order`` no livro — é consumida no caminho e o que sobra é
    descartado. O que existe em toda execução é o lado da agressão, e é ele que
    ``taker_side`` guarda. Um campo opcional, preenchido só quando o taker calhasse de
    repousar, seria pior de duas maneiras: obrigaria todo leitor a tratar o ``None`` e
    ainda mentiria por omissão, já que ``None`` diria "não havia ordem" onde o fato é
    "não havia identidade a atribuir".
    """

    price: Ticks
    quantity: int
    maker_order_id: OrderId
    taker_side: Side


@dataclass(frozen=True, slots=True)
class OrderAccepted:
    """Ordem que passou a repousar no livro, com o saldo que de fato ficou lá.

    ``quantity`` é o que repousou, e não o que o cliente pediu: uma limit agressiva
    executa parte contra o lado oposto e deixa só o resto no livro, e é o resto que o
    livro passa a oferecer a quem chegar depois. O que foi executado já está descrito nos
    ``Trade`` que precedem este evento na mesma lista, então repetir a quantidade original
    aqui seria contar a mesma quantidade duas vezes.

    ``price`` não é opcional porque este evento relata entrada no livro, e o livro é
    indexado por preço: uma ordem sem preço não repousa em nível nenhum.
    """

    order_id: OrderId
    side: Side
    price: Ticks
    quantity: int


type Event = Trade | OrderAccepted
"""Tudo que a engine pode devolver de um comando."""
