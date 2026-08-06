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


@dataclass(frozen=True, slots=True)
class OrderCancelled:
    """Ordem retirada do livro a pedido do cliente, antes de executar por inteiro.

    ``remaining`` é o remanescente no instante do cancelamento, isto é, a liquidez que saiu
    do livro. Ele não aparece na saída: o enunciado fixa ``Order cancelled`` como linha
    inteira, sem id e sem quantidade. Está aqui porque é o que torna o evento auditável —
    sem ele, o histórico registra que algo foi cancelado sem dizer o que deixou de estar
    disponível, e a conservação de quantidade entre o que entrou e o que saiu do livro
    deixa de fechar. O evento serve ao domínio; o que a tela mostra é recorte do presenter.

    ``price`` é opcional porque uma ordem pegged *parked* pode ser cancelada sem nunca ter
    tido preço — ela espera referência fora dos dois lados do livro, e cancelar é
    justamente uma das coisas que o cliente pode querer fazer com ela enquanto espera.
    """

    order_id: OrderId
    side: Side
    price: Ticks | None
    remaining: int


@dataclass(frozen=True, slots=True)
class OrderAmended:
    """Ordem alterada a pedido do cliente, com o que ficou no livro depois da alteração.

    ``quantity`` é o que repousa agora, pela mesma razão de ``OrderAccepted``: um amend que
    torna a ordem marketable executa parte contra o lado oposto, e os ``Trade`` que
    precedem este evento na mesma lista já relatam essa parte. Repetir aqui a quantidade
    pedida contaria a mesma quantidade duas vezes.

    ``price`` não é opcional porque o evento relata uma ordem que ficou no livro, e o livro
    é indexado por preço — uma ordem inteiramente executada pelo amend não produz este
    evento, produz só os seus ``Trade``.

    ``priority_renewed`` registra se a ordem perdeu o lugar na fila. Não aparece na saída, e
    está aqui porque é o que torna o evento auditável: sem ele, dois amends de efeito oposto
    sobre a fila ficariam indistinguíveis no histórico. Uma ordem de 300 reduzida para 200 e
    uma de 100 aumentada para 200 emitem o mesmo lado, o mesmo preço e a mesma quantidade, e
    só a segunda foi para o fim da fila — a pergunta "por que esta ordem executou depois
    daquela" deixaria de ter resposta nos eventos. A política que decide o valor está na ADR
    0005.
    """

    order_id: OrderId
    side: Side
    price: Ticks
    quantity: int
    priority_renewed: bool


@dataclass(frozen=True, slots=True)
class BookEntry:
    """Uma ordem viva na visualização do livro: o que resta dela, ao preço em que está.

    ``quantity`` é o remanescente, e não a quantidade original: o livro oferece o que
    sobrou, e é isso que quem lê a tela precisa poder acreditar.
    """

    quantity: int
    price: Ticks


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    """O livro inteiro no instante em que foi pedido, dos dois lados, em ordem de prioridade.

    É **valor**, não vista. Tuplas imutáveis de escalares, sem referência ao ``OrderBook``
    — o mesmo motivo pelo qual nenhum evento carrega ``Order`` viva. Um snapshot que
    apontasse para o livro seria formatado depois de ser produzido, e mostraria o estado do
    momento da formatação, não o do momento em que foi pedido. Como valor, o que se imprime
    é o que se pediu, aconteça o que acontecer com o livro no meio.

    O conteúdo é ordem a ordem, e não agregado por nível de preço, e a diferença é
    substantiva. O exemplo de pegged do enunciado exibe ``150 @ 10.1`` e ``300 @ 10.1`` em
    linhas separadas: são duas ordens no mesmo preço, e é a separação que torna a fila FIFO
    visível na tela. Agregar mostraria ``450 @ 10.1`` e destruiria a evidência dos
    requisitos 2 e 4 — não se veria mais quem está na frente da fila, nem o efeito de uma
    alteração sobre a prioridade. A soma por nível existe em ``PriceLevel.total_quantity``
    para quem precisar dela; a visualização precisa do contrário.
    """

    bids: tuple[BookEntry, ...]
    asks: tuple[BookEntry, ...]


type Event = Trade | OrderAccepted | OrderCancelled | OrderAmended | BookSnapshot
"""Tudo que a engine pode devolver de um comando."""
