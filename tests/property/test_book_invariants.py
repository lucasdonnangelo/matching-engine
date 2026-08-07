"""Propriedade: as invariantes do livro valem depois de **cada** comando.

Um teste de exemplo afirma que uma sequência escolhida a dedo produz o resultado esperado.
Este afirma outra coisa: que nenhuma sequência de comandos bem formados — qualquer ordem,
qualquer intercalação, qualquer quantidade — deixa o livro num estado que o contrato
proíbe. Os exemplos cobrem o que se pensou em cobrir; a propriedade cobre o que não se
pensou, que é justamente onde moram os bugs de estrutura de dados intrusiva.

As sete invariantes da seção 4 do contrato aparecem abertas em onze conferências, porque
várias delas se decompõem em fatos que falham por motivos diferentes e merecem mensagens
diferentes. O mapa está em ``assert_invariants``.

O que este arquivo **não** faz é conferir resultado: não há aqui nenhuma afirmação sobre
qual ordem executou contra qual, nem sobre o texto de saída. Isso é dos testes unitários e
dos golden de ponta a ponta. Aqui só se pergunta se o livro continua sendo um livro.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import assert_never

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from matching_engine.domain.engine import MatchingEngine
from matching_engine.domain.events import Event, Trade
from matching_engine.domain.order import Order, OrderId
from matching_engine.domain.order_book import OrderBook
from matching_engine.domain.order_queue import OrderQueue
from matching_engine.domain.price import Ticks
from matching_engine.domain.side import PegReference, Side

# --------------------------------------------------------------------------------------
# Descritores de comando
# --------------------------------------------------------------------------------------
#
# São dados, não chamadas: o Hypothesis precisa poder gerar, encolher e **imprimir** a
# sequência que falhou, e uma lista de dataclasses se lê no relatório de falha como o
# roteiro que reproduz o bug. Uma lista de lambdas ou de tuplas cruas apareceria como
# ``<function <lambda> at 0x...>``, e o exemplo mínimo que o Hypothesis encontrou seria
# ilegível justamente no momento em que ele importa.


@dataclass(frozen=True, slots=True)
class SubmitLimit:
    side: Side
    price_ticks: int
    quantity: int


@dataclass(frozen=True, slots=True)
class SubmitMarket:
    side: Side
    quantity: int


@dataclass(frozen=True, slots=True)
class SubmitPeg:
    """O lado não é gerado: ele **é** a referência.

    Peg cruzado é recusado na construção da ``Order``, então gerar os dois campos de forma
    independente produziria, em três quartos dos casos, um comando que a engine rejeita
    antes de tocar no livro — sequências longas de recusa gastando exemplo do Hypothesis
    sem exercitar nada. A lateralidade do peg tem teste próprio, unitário.
    """

    reference: PegReference
    quantity: int

    @property
    def side(self) -> Side:
        return self.reference.side


@dataclass(frozen=True, slots=True)
class CancelNth:
    """Cancela a n-ésima ordem viva, e não um ``order_id`` sorteado.

    Id sorteado quase nunca acerta uma ordem viva, e o comando viraria uma linha de erro:
    o teste passaria a exercitar a mensagem de "ordem não está no livro" em vez do
    cancelamento. A resolução por posição amarra o comando ao estado que o livro tem
    naquele instante — ver ``_nth_live_order_id``.
    """

    n: int


@dataclass(frozen=True, slots=True)
class AmendNth:
    """Pelo menos um entre preço e quantidade é não-``None``, por construção.

    A engine recusa — com razão — a alteração que não pede nada, e gerar esse caso pela
    combinação livre de dois opcionais produziria um quarto de comandos inúteis. Ver
    ``AMENDMENTS``, onde os três formatos válidos são estratégias separadas em vez de um
    filtro sobre o produto dos dois campos.
    """

    n: int
    price_ticks: int | None
    quantity: int | None


type Command = SubmitLimit | SubmitMarket | SubmitPeg | CancelNth | AmendNth


# --------------------------------------------------------------------------------------
# Estratégias
# --------------------------------------------------------------------------------------

PRICE_TICKS = st.integers(min_value=995, max_value=1015)
"""Faixa estreita de propósito.

Preço aleatório num intervalo largo quase nunca colide, e sem colisão o teste não exercita
FIFO dentro do nível, ciclo de vida de nível nem reprecificação de pegged: cada ordem
criaria o seu próprio nível, viveria sozinha nele e sairia sem nunca disputar fila com
ninguém. Vinte e um ticks sobre dezenas de comandos garantem colisão de preço, níveis que
esvaziam e voltam a existir, e um topo que se move para os dois lados. A restrição é o que
dá poder ao teste — junto com o comprimento da sequência, ver ``COMMANDS``.
"""

QUANTITIES = st.integers(min_value=1, max_value=100)
TARGETS = st.integers(min_value=0, max_value=50)
SIDES = st.sampled_from(Side)
REFERENCES = st.sampled_from(PegReference)

AMENDMENTS = st.one_of(
    st.builds(AmendNth, n=TARGETS, price_ticks=PRICE_TICKS, quantity=st.none()),
    st.builds(AmendNth, n=TARGETS, price_ticks=st.none(), quantity=QUANTITIES),
    st.builds(AmendNth, n=TARGETS, price_ticks=PRICE_TICKS, quantity=QUANTITIES),
)

COMMAND = st.one_of(
    st.builds(SubmitLimit, side=SIDES, price_ticks=PRICE_TICKS, quantity=QUANTITIES),
    st.builds(SubmitMarket, side=SIDES, quantity=QUANTITIES),
    st.builds(SubmitPeg, reference=REFERENCES, quantity=QUANTITIES),
    st.builds(CancelNth, n=TARGETS),
    AMENDMENTS,
)

COMMANDS = st.one_of(
    st.lists(COMMAND, min_size=1, max_size=60),
    st.lists(COMMAND, min_size=25, max_size=60),
)
"""Sequências curtas e longas, em dois ramos, e não numa faixa só de 1 a 60.

``st.lists(min_size=1, max_size=60)`` **não** produz sessenta comandos: a distribuição do
Hypothesis mira uma média por volta de meia dúzia, e o teto é só um teto. Medido sobre
trezentos exemplos, o ramo curto sozinho dá cerca de sete comandos por sequência, e um
livro de sete comandos quase nunca chega a ter os dois lados povoados ao mesmo tempo — o
matching, que é a parte difícil, fica praticamente sem exercício.

O ramo longo garante o regime que o teto anuncia: livro com vários níveis dos dois lados,
fila com mais de uma ordem por preço, pegged dividindo nível com a não-pegged que lhes dá
referência, e agressivas encontrando contraparte de verdade.

O ramo curto fica, e fica **primeiro**, porque é para onde ``one_of`` encolhe: o exemplo
mínimo que o Hypothesis reporta continua sendo uma sequência de poucos comandos, que é o
que se consegue ler e reproduzir na mão. Cobertura vem do ramo longo, legibilidade da
falha vem do curto.
"""


# --------------------------------------------------------------------------------------
# Execução
# --------------------------------------------------------------------------------------


def replay(commands: Sequence[Command]) -> MatchingEngine:
    """Executa a sequência numa engine nova, conferindo tudo depois de cada comando.

    Engine nova a cada execução: o Hypothesis reexecuta o mesmo teste centenas de vezes, e
    estado sobrevivente entre exemplos tornaria a falha dependente da ordem em que os
    exemplos saíram — o oposto de um teste que se reproduz a partir do relatório.

    Devolve a engine porque um dos testes precisa continuar operando sobre o livro que a
    sequência produziu; os outros dois ignoram o retorno.
    """
    engine = MatchingEngine()
    for command in commands:
        target: OrderId | None = None
        if isinstance(command, CancelNth | AmendNth):
            target = _nth_live_order_id(engine.book, command.n)
            if target is None:
                # Livro vazio: não há ordem a cancelar nem a alterar. Inventar um id aqui
                # exercitaria a mensagem de "ordem não está no livro", que é assunto de
                # teste unitário, e não o comando que este arquivo quer exercitar.
                continue

        quantity_before = {side: live_quantity(engine.book, side) for side in Side}

        try:
            events = _execute(engine, command, target)
        except ValueError:
            # Recusa legítima de um pedido do cliente: preço, quantidade ou estado que a
            # engine não admite. É o que o ``Cli`` transforma numa linha ``Error:`` sem
            # encerrar a sessão, então aqui o comando é dado por não executado e a
            # sequência continua — inclusive porque as invariantes têm de valer também
            # depois de um comando recusado.
            #
            # Não existe um ``except RuntimeError`` ao lado deste, e a ausência é o ponto:
            # a família de integridade — ``QueueIntegrityError``, ``LevelIntegrityError``,
            # ``BookIntegrityError``, ``OrderIntegrityError`` — descreve a engine tendo
            # perdido a conta de si mesma, e nenhum comando bem formado alcança essas
            # exceções. Capturá-las esconderia exatamente o bug que este arquivo procura.
            events = []

        assert_conservation(command, quantity_before, engine.book, events)
        assert_invariants(engine.book)

    return engine


def _execute(engine: MatchingEngine, command: Command, target: OrderId | None) -> list[Event]:
    """Traduz o descritor na chamada de engine correspondente.

    ``target`` já vem resolvido de ``replay``, e não é resolvido aqui, porque quem decide
    pular o comando quando não há ordem viva é o laço: uma resolução tardia teria de
    devolver "não executei" por um caminho separado do retorno de eventos.
    """
    match command:
        case SubmitLimit():
            return engine.submit_limit(command.side, Ticks(command.price_ticks), command.quantity)
        case SubmitMarket():
            return engine.submit_market(command.side, command.quantity)
        case SubmitPeg():
            return engine.submit_pegged(command.reference, command.side, command.quantity)
        case CancelNth():
            assert target is not None
            return engine.cancel(target)
        case AmendNth():
            assert target is not None
            return engine.amend(
                target,
                None if command.price_ticks is None else Ticks(command.price_ticks),
                command.quantity,
            )
        case _:
            assert_never(command)


def _nth_live_order_id(book: OrderBook, n: int) -> OrderId | None:
    """A n-ésima ordem viva por id, ciclicamente; ``None`` se o livro está vazio.

    A ordenação por id, e não a de descoberta no livro, é o que torna o alvo função apenas
    de ``n`` e do conjunto de ordens vivas. Fosse a ordem em que os níveis são percorridos,
    o mesmo ``n`` passaria a apontar para ordens diferentes conforme o preço se movesse, e o
    exemplo mínimo que o Hypothesis reporta deixaria de reproduzir a falha.
    """
    ids = sorted(order.order_id for order in _live_orders(book))
    return None if not ids else ids[n % len(ids)]


def _live_orders(book: OrderBook, side: Side | None = None) -> list[Order]:
    """Toda ordem viva alcançável pela estrutura: as dos níveis dos dois lados e as parked.

    São os dois lugares onde uma ordem pode estar, e a lista é montada a partir deles — não
    a partir do índice global. É essa direção que faz do índice global algo a **conferir**,
    em ``_assert_global_index_matches``, em vez de a fonte da verdade que se confere contra
    si mesma.
    """
    sides = list(Side) if side is None else [side]
    orders = [order for each in sides for level in book.side(each) for order in level]
    orders.extend(order for order in book.parked_orders if side is None or order.side is side)
    return orders


def live_quantity(book: OrderBook, side: Side) -> int:
    """Quantidade viva de um lado: a dos níveis **mais** a das ordens parked daquele lado.

    As parked entram na conta porque estacionar não destrói quantidade: a ordem sai do
    índice de preços e continua viva, cancelável pelo id, e volta ao livro assim que surgir
    referência. Contar só os níveis faria a conservação acusar sumiço de quantidade toda vez
    que uma agressiva consumisse a última não-pegged de um lado e mandasse as pegged para
    *parked* — um falso positivo que dependeria do tipo de ordem, e não do cruzamento.
    """
    in_levels = sum(level.total_quantity for level in book.side(side))
    parked = sum(order.remaining for order in book.parked_orders if order.side is side)
    return in_levels + parked


def assert_conservation(
    command: Command,
    quantity_before: dict[Side, int],
    book: OrderBook,
    events: Sequence[Event],
) -> None:
    """Item 5 dos invariantes na sua forma checável: nada some, nada aparece no cruzamento.

    O item fala em "Σ quantidade executada do agressor == Σ quantidade consumida dos
    passivos", e o agressor não é observável depois do comando — uma market nunca chega a
    ser ordem no livro, e uma limit agressiva só deixa o remanescente. O lado passivo, esse,
    é observável: a queda da quantidade viva do lado **oposto** ao da ordem enviada é
    exatamente o que os passivos entregaram, e a soma dos ``Trade`` é exatamente o que o
    agressor levou. Igualar as duas é a mesma afirmação, medida de fora.

    Só vale para submissão. Cancelar retira quantidade do livro sem que ela tenha sido
    negociada, e reduzir por amend também — nos dois casos a quantidade não sumiu, ela
    deixou de ser oferecida, que é outro fato e tem teste próprio.
    """
    if not isinstance(command, SubmitLimit | SubmitMarket | SubmitPeg):
        return

    opposite = command.side.opposite
    consumed = quantity_before[opposite] - live_quantity(book, opposite)
    traded = sum(event.quantity for event in events if isinstance(event, Trade))
    assert consumed == traded, (
        f"conservação quebrada em {command}: o lado {opposite.name} perdeu {consumed} de "
        f"quantidade viva, mas os trades do comando somam {traded}"
    )


# --------------------------------------------------------------------------------------
# Invariantes
# --------------------------------------------------------------------------------------


def assert_invariants(book: OrderBook) -> None:
    """As sete invariantes da seção 4 do contrato, abertas nas onze conferências que as compõem.

    ==========================  ==================================================
    Seção 4                     Conferência
    ==========================  ==================================================
    1. livro não cruzado        ``_assert_not_crossed``
    2. sem nível vazio          ``_assert_levels_are_sound``
    3. total do nível           ``_assert_levels_are_sound``
    4. ordem viva num lugar só  ``_assert_global_index_matches``
    5. conservação              ``assert_conservation``, por comando
    6. pegged na referência     ``_assert_pegged_are_at_reference``
    7. um nível pegged-only     ``_assert_levels_are_sound``
    ==========================  ==================================================

    ``_assert_levels_are_sound`` carrega ainda o que a seção 4 não numera mas de que ela
    depende: o contador de não-pegged, o pertencimento de fila, o encadeamento duplo e a
    ordem de chegada dentro do nível. São os fatos estruturais que sustentam os outros —
    um encadeamento partido não viola nenhuma das sete diretamente, e faz todas as sete
    passarem a valer sobre metade da fila.
    """
    _assert_not_crossed(book)
    for side in Side:
        _assert_levels_are_sound(book, side)
    _assert_global_index_matches(book)
    _assert_pegged_are_at_reference(book)
    _assert_parked_are_pegged_without_price(book)


def _assert_not_crossed(book: OrderBook) -> None:
    """Item 1: o livro nunca fica cruzado."""
    best_bid, best_ask = book.best_bid, book.best_ask
    if best_bid is None or best_ask is None:
        return
    assert best_bid < best_ask, (
        f"livro cruzado: a melhor compra {best_bid} alcança a melhor venda {best_ask}, e as "
        f"duas continuam repousadas em vez de terem executado"
    )


def _assert_levels_are_sound(book: OrderBook, side: Side) -> None:
    """Itens 2, 3 e 7, mais os fatos estruturais do nível e da fila.

    Um laço só, e não um por conferência, porque todas leem a mesma travessia: separá-las
    custaria sete percursos do lado para produzir exatamente as mesmas listas.
    """
    queues: list[OrderQueue] = []
    pegged_only: list[int] = []

    for position, level in enumerate(book.side(side)):
        orders = list(level)
        where = f"nível {level.price} do lado {side.name}"

        # Item 2: nível vazio não permanece no índice de preços.
        assert orders, f"{where} está vazio e continua no índice de preços"
        assert len(level) == len(orders), (
            f"{where} diz ter {len(level)} ordens, mas o percurso da fila encontra "
            f"{len(orders)}"
        )

        # Item 3.
        total = sum(order.remaining for order in orders)
        assert level.total_quantity == total, (
            f"{where} anuncia total de {level.total_quantity}, mas os remanescentes das "
            f"suas ordens somam {total}"
        )

        non_pegged = sum(1 for order in orders if not order.is_pegged)
        assert level.non_pegged_count == non_pegged, (
            f"{where} conta {level.non_pegged_count} ordens não-pegged, mas a fila tem "
            f"{non_pegged}"
        )
        if non_pegged == 0:
            pegged_only.append(position)

        for order in orders:
            assert order.price == level.price, (
                f"ordem {order.order_id} tem preço {order.price} e está guardada no {where}"
            )

        _assert_queue_is_consistent(level_orders=orders, where=where, queues=queues)

        for previous, order in pairwise(orders):
            assert previous.sequence_id < order.sequence_id, (
                f"{where} fora da ordem de chegada: a ordem {previous.order_id}, de "
                f"sequência {previous.sequence_id}, está à frente da ordem "
                f"{order.order_id}, de sequência {order.sequence_id}"
            )

    # Item 7. O lado é percorrido do melhor para o pior preço, então o topo é a posição 0.
    assert len(pegged_only) <= 1, (
        f"lado {side.name} tem {len(pegged_only)} níveis compostos só de ordens pegged, nas "
        f"posições {pegged_only}: o item 7 admite no máximo um"
    )
    if pegged_only:
        assert pegged_only[0] == 0, (
            f"lado {side.name} tem um nível só de ordens pegged na posição {pegged_only[0]}, "
            f"e não no topo"
        )


def _assert_queue_is_consistent(
    level_orders: list[Order], where: str, queues: list[OrderQueue]
) -> None:
    """Pertencimento e encadeamento duplo da fila do nível.

    O nível não expõe a sua ``OrderQueue``, e não precisa expor: a alça da primeira ordem
    **é** a fila do nível, e o que dá sentido a usá-la como referência é a conferência de
    unicidade que vem junto — nenhuma fila serve a dois níveis. Sem ela, um nível que
    tivesse adotado a fila de outro passaria, porque todas as suas ordens concordariam entre
    si sobre a alça errada.

    O percurso de volta existe porque o encadeamento é duplo e as duas direções são
    mantidas por atribuições distintas em ``OrderQueue``. Um ``prev`` desatualizado não
    aparece na leitura para a frente — que é a que o livro usa para imprimir e o matching
    para consumir — e só se manifesta muito depois, na remoção de uma ordem do meio, que
    religa os vizinhos errados.
    """
    queue = level_orders[0].queue
    assert queue is not None, (
        f"ordem {level_orders[0].order_id} está no {where} sem alça de fila nenhuma"
    )
    assert not any(queue is seen for seen in queues), (
        f"o {where} compartilha a sua fila com outro nível do mesmo lado"
    )
    queues.append(queue)

    for order in level_orders:
        assert order.queue is queue, (
            f"ordem {order.order_id} está no {where}, mas a sua alça aponta para outra fila"
        )
    assert len(queue) == len(level_orders), (
        f"a fila do {where} diz ter {len(queue)} ordens e o percurso encontra "
        f"{len(level_orders)}"
    )

    assert level_orders[0].prev is None, (
        f"a cabeça da fila do {where}, ordem {level_orders[0].order_id}, tem antecessor"
    )
    assert level_orders[-1].next is None, (
        f"a cauda da fila do {where}, ordem {level_orders[-1].order_id}, tem sucessor"
    )

    backwards: list[Order] = []
    current: Order | None = level_orders[-1]
    while current is not None:
        backwards.append(current)
        current = current.prev
    backwards.reverse()
    # ``Order`` tem ``eq=False``: a comparação das listas é por identidade, que é o que se
    # quer — duas ordens de campos iguais são ordens diferentes.
    assert backwards == level_orders, (
        f"o encadeamento do {where} não fecha nos dois sentidos: para a frente "
        f"{[order.order_id for order in level_orders]}, para trás "
        f"{[order.order_id for order in backwards]}"
    )


def _assert_global_index_matches(book: OrderBook) -> None:
    """Item 4: toda ordem viva está no índice global e em exatamente um nível, ou parked.

    As três afirmações se cobrem mutuamente. Ids distintos garantem que nenhuma ordem
    aparece em dois lugares; a identidade em ``get`` garante que cada ordem alcançável está
    indexada, e indexada como ela mesma e não como uma homônima; e o tamanho fecha a
    direção que falta, a de o índice guardar alguém que nenhum nível nem o registro de
    parked guardam — a ordem fantasma, que nenhuma das outras duas veria.
    """
    orders = _live_orders(book)
    ids = [order.order_id for order in orders]
    assert len(set(ids)) == len(ids), (
        f"a mesma ordem está viva em mais de um lugar do livro: ids {sorted(ids)}"
    )
    for order in orders:
        assert book.get(order.order_id) is order, (
            f"ordem {order.order_id} está no livro, mas o índice global devolve outra coisa "
            f"para o seu id"
        )
    assert len(book) == len(orders), (
        f"o índice global tem {len(book)} ordens, mas os níveis e o registro de parked "
        f"guardam {len(orders)}"
    )


def _assert_pegged_are_at_reference(book: OrderBook) -> None:
    """Item 6: toda pegged está no melhor preço não-pegged do seu lado, ou parked.

    A conferência de *parked* é mais estrita do que a letra do item, de propósito: exige que
    a ordem esteja estacionada **porque** não há referência, e não apenas que esteja
    estacionada. Lida ao pé da letra, a alternativa "ou parked" seria uma saída livre —
    estacionar toda pegged satisfaria o item e destruiria o tipo de ordem. O que a
    reconciliação promete é o bicondicional: sem referência, parked; com referência, no
    preço dela.
    """
    for side in Side:
        reference = _best_non_pegged_price(book, side)
        for order in _live_orders(book, side):
            if not order.is_pegged:
                continue
            if order.price is None:
                assert reference is None, (
                    f"ordem pegged {order.order_id} está parked, mas o lado {side.name} tem "
                    f"referência de preço {reference}"
                )
                continue
            assert order.price == reference, (
                f"ordem pegged {order.order_id} está a {order.price}, e o melhor preço "
                f"não-pegged do lado {side.name} é {reference}"
            )


def _best_non_pegged_price(book: OrderBook, side: Side) -> Ticks | None:
    """O melhor preço não-pegged do lado, recalculado por varredura.

    Varre de propósito. A conferência não pode se apoiar em ``BookSide.best_non_pegged_price``,
    que é parte do que está sendo verificado: um erro naquela consulta — ela olha no máximo
    dois níveis do topo — concordaria consigo mesmo e passaria. A varredura é O(P) e proibida
    no domínio pela seção 5 do contrato; aqui é teste, e o que se pede dele é independência,
    não velocidade.

    Também não usa ``non_pegged_count``, e sim as próprias ordens, para não depender de um
    contador que é ele mesmo objeto de conferência.
    """
    for level in book.side(side):
        if any(not order.is_pegged for order in level):
            return level.price
    return None


def _assert_parked_are_pegged_without_price(book: OrderBook) -> None:
    """Estacionar é estado exclusivo da pegged sem referência: não há outra ordem sem preço."""
    for order in book.parked_orders:
        assert order.is_pegged, (
            f"ordem {order.order_id} está parked sem ser pegged: só a pegged sem referência "
            f"vive fora dos dois lados"
        )
        assert order.price is None, (
            f"ordem {order.order_id} está parked com preço {order.price}, e preço nenhum é "
            f"a definição do estado"
        )


# --------------------------------------------------------------------------------------
# Testes
# --------------------------------------------------------------------------------------


@given(COMMANDS)
@settings(max_examples=300, deadline=None)
def test_invariants_hold_after_every_command(commands: list[Command]) -> None:
    """As onze conferências passam depois de cada comando de qualquer sequência bem formada.

    ``deadline=None``: o prazo por exemplo do Hypothesis causa falha intermitente em runner
    de CI mais lento que a máquina de desenvolvimento — o mesmo teste, o mesmo código, e um
    exemplo que passa aqui e estoura o prazo lá. O que este teste mede é corretude, não
    latência; as metas de tempo da seção 5 do contrato são de complexidade assintótica, e
    não se verificam cronometrando sessenta comandos.
    """
    replay(commands)


@given(COMMANDS)
@settings(max_examples=100, deadline=None)
def test_the_book_empties_completely_when_everything_is_cancelled(
    commands: list[Command],
) -> None:
    """Cancelar tudo devolve o livro ao estado inicial, e não a um estado parecido com ele.

    É a conferência que nenhuma invariante isolada faz: elas descrevem coerência interna, e
    um livro coerente pode carregar lixo — nível vazio que sobreviveu, ordem no índice
    global que nenhum nível guarda, pegged estacionada que ninguém reativa. Esvaziar por
    cancelamento força cada estrutura a passar pelo caminho de saída, e o que sobrar
    aparece aqui.
    """
    engine = replay(commands)

    for _ in range(len(engine.book)):
        order_id = _nth_live_order_id(engine.book, 0)
        assert order_id is not None, (
            "o índice global ainda tem ordens, mas nenhuma delas está num nível nem parked"
        )
        engine.cancel(order_id)
        assert_invariants(engine.book)

    book = engine.book
    assert len(book) == 0, f"o índice global ficou com {len(book)} ordens depois de cancelar tudo"
    assert book.best_bid is None, f"o lado comprador ainda anuncia topo {book.best_bid}"
    assert book.best_ask is None, f"o lado vendedor ainda anuncia topo {book.best_ask}"
    for side in Side:
        assert len(book.side(side)) == 0, (
            f"o lado {side.name} ficou com {len(book.side(side))} níveis sem ordem nenhuma"
        )
    assert book.parked_orders == (), (
        f"sobraram {len(book.parked_orders)} ordens parked depois de cancelar tudo"
    )


@given(COMMANDS)
@settings(max_examples=200, deadline=None)
def test_no_integrity_error_escapes(commands: list[Command]) -> None:
    """Separa "o usuário digitou errado" de "a engine perdeu a conta de si mesma".

    A seção 6 do contrato divide as exceções por **origem**: ``ValueError`` é pedido que a
    engine recusa, e o REPL responde com uma linha de erro e segue; ``RuntimeError`` é
    inconsistência interna, e comando bem formado nunca deveria alcançá-la. Todo comando
    gerado aqui é bem formado por construção — lado válido, preço inteiro e positivo,
    quantidade positiva, id de ordem viva —, então qualquer exceção da família de
    integridade que escape é a afirmação da seção 6 sendo falsa.

    ``replay`` já não captura ``RuntimeError``, e por isso este teste seria redundante como
    verificação. Ele não é redundante como **declaração**: sem ele, a ausência de um
    ``except`` é uma omissão que alguém pode "corrigir" ao endurecer o replay contra ruído,
    e a distinção que a seção 6 desenha deixaria de ter teste algum.
    """
    try:
        replay(commands)
    except RuntimeError as error:
        pytest.fail(
            f"exceção de integridade escapou de uma sequência de comandos bem formados: "
            f"{type(error).__name__}: {error}"
        )
