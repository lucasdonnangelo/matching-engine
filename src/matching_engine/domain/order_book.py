"""O livro: os dois lados, o índice global de ordens vivas e a prioridade temporal.

Aqui termina o livro e começa a engine. O livro sabe guardar uma ordem, achá-la pelo id,
retirá-la e dizer qual é o topo de cada lado. Não sabe cruzar ordens: decidir que uma
compra encontra uma venda, a que preço, em que quantidade e contra quem primeiro é
matéria da engine, que opera *sobre* esta estrutura em vez de morar dentro dela.

A fronteira paga duas contas. O livro se testa sem engine nenhuma — ``add``, ``remove`` e
os topos se verificam com ordens fabricadas na mão —, e o matching, que é a parte difícil
de ler, fica livre de manutenção de índice: ele pede o topo, executa e manda retirar.
"""

from __future__ import annotations

from matching_engine.domain.book_side import BookSide
from matching_engine.domain.order import Order, OrderId
from matching_engine.domain.price import Ticks
from matching_engine.domain.side import PegReference, Side


class BookIntegrityError(RuntimeError):
    """Índice global inconsistente: ``order_id`` repetido, ou ordem que não está nele.

    ``RuntimeError`` pelo mesmo critério de ``QueueIntegrityError`` e
    ``LevelIntegrityError``: o id é gerado pelo próprio livro, então repetição não é
    entrada malformada do usuário — é a engine perdendo a conta.
    """


class OrderBook:
    """Os dois lados, o índice global por ``order_id`` e os contadores monotônicos.

    O contador de ``sequence_id`` vive aqui, e não na engine, porque ele existe para
    ordenar a fila, e a fila é do livro. Na engine, qualquer teste do livro teria de
    passar pela engine só para que uma ordem nascesse com prioridade válida, e a
    estrutura que depende do número ficaria dependente de quem não é dono dele.

    ``order_id`` e ``sequence_id`` têm contadores **separados** porque são consumidos em
    ritmos diferentes: o amend que muda preço ou aumenta quantidade renova a prioridade
    — consome sequência — sem criar ordem nova, já que o cliente continua se referindo à
    ordem pelo mesmo id. Com um contador só, ou o id saltaria a cada amend, ou a
    prioridade ficaria presa à identidade e o amend não teria como pagar o seu preço.

    ``_parked`` é o terceiro lugar onde uma ordem pode estar, ao lado dos dois lados: as
    pegged à espera de referência. Elas contam em ``__len__`` e são achadas por ``get``
    como qualquer outra, porque estão vivas — ver ``park``.
    """

    __slots__ = ("_asks", "_bids", "_next_order_id", "_next_sequence", "_orders", "_parked")

    def __init__(self) -> None:
        self._bids = BookSide(Side.BUY)
        self._asks = BookSide(Side.SELL)
        self._orders: dict[OrderId, Order] = {}
        self._parked: dict[OrderId, Order] = {}
        self._next_sequence = 1
        self._next_order_id = 1

    def side(self, side: Side) -> BookSide:
        """Lado pedido do livro."""
        return self._bids if side is Side.BUY else self._asks

    def opposite_side(self, side: Side) -> BookSide:
        """Lado contra o qual uma ordem deste lado procura contraparte."""
        return self.side(side.opposite)

    @property
    def best_bid(self) -> Ticks | None:
        """Maior preço de compra; ``None`` se não há compra no livro.

        O item 1 dos invariantes — ``best_bid < best_ask`` — é preservado pelo matching,
        não por este módulo, que apenas expõe os dois topos. Um livro cruzado é
        construível chamando ``add`` direto, e é assim que os testes da engine vão montar
        o cenário que ela precisa desfazer. O livro não defende o que não decide.
        """
        return self._bids.best_price

    @property
    def best_ask(self) -> Ticks | None:
        """Menor preço de venda; ``None`` se não há venda no livro.

        Vale a mesma ressalva de ``best_bid``: o cruzamento é assunto do matching.
        """
        return self._asks.best_price

    def create_order(
        self,
        side: Side,
        price: Ticks | None,
        quantity: int,
        peg_reference: PegReference | None = None,
    ) -> Order:
        """Fabrica uma ordem com id e prioridade novos, **sem** inseri-la no livro.

        Fabricar e inserir são passos distintos porque nem toda ordem criada repousa: uma
        agressiva pode ser inteiramente executada e nunca chegar a um nível, e uma pegged
        sem referência nasce *parked*, fora dos dois lados. Se a criação já inserisse, a
        engine teria de retirar do livro, no caminho comum, uma ordem que nunca deveria
        ter entrado.

        Os contadores só avançam depois de a ordem existir: uma construção recusada pelos
        invariantes da ``Order`` não abre buraco na sequência nem queima um id.
        """
        order = Order(
            order_id=OrderId(self._next_order_id),
            sequence_id=self._next_sequence,
            side=side,
            price=price,
            quantity=quantity,
            peg_reference=peg_reference,
        )
        self._next_order_id += 1
        self._next_sequence += 1
        return order

    def next_sequence(self) -> int:
        """Consome o próximo número de prioridade.

        Existe para o amend, que devolve ao fim da fila uma ordem já viva: a identidade
        dela não muda, só o lugar, e é a sequência que diz o lugar.
        """
        sequence = self._next_sequence
        self._next_sequence += 1
        return sequence

    def add(self, order: Order) -> None:
        """Insere a ordem no lado correspondente e no índice global. O(log P).

        O lado recebe a ordem antes de o índice registrá-la: é o lado que recusa ordem
        *parked*, lateralidade trocada e ordem já enfileirada, e registrar antes deixaria
        no índice global uma ordem que o livro não guarda em nível nenhum.
        """
        if order.order_id in self._orders:
            raise BookIntegrityError(f"order_id {order.order_id} já está no índice global")

        self.side(order.side).add(order)
        self._orders[order.order_id] = order

    def remove(self, order: Order) -> None:
        """Retira a ordem do livro inteiro. O(1) esperado, O(log P) se o nível esvaziar.

        É aqui que o item 2 dos invariantes se cumpre. São três baixas — a ordem sai do
        nível, o nível vazio sai do índice de preços, a ordem sai do índice global — e
        elas moram na mesma operação porque é exatamente a divisão delas que produz
        estado fantasma: nível vazio anunciando preço sem quantidade por trás, ou ordem no
        índice global que nível nenhum guarda.

        A checagem do índice vem antes de qualquer mutação, e compara identidade: retirar
        pelo id uma ordem homônima apagaria do índice a entrada de outra ordem, viva, que
        continuaria no nível — o fantasma exato que este método existe para não criar.

        A ordem *parked* desvia para ``unpark`` porque as três baixas não se aplicam a
        ela: ela não está em nível nenhum, logo não há nível a esvaziar nem preço a
        consultar. Sem o desvio, ``BookSide.remove`` a recusaria — corretamente, e por um
        motivo que não é o dela —, e cancelar uma pegged à espera de referência viraria
        erro interno. Quem chama ``cancel`` não sabe, nem precisa saber, se a ordem está
        no livro ou estacionada: é o mesmo id e a mesma retirada.
        """
        if self._orders.get(order.order_id) is not order:
            raise BookIntegrityError(f"ordem {order.order_id} não está no índice global")

        if self._parked.get(order.order_id) is order:
            self.unpark(order)
            return

        side = self.side(order.side)
        level = None if order.price is None else side.level_at(order.price)
        side.remove(order)
        if level is not None:
            side.remove_if_empty(level)
        del self._orders[order.order_id]

    def park(self, order: Order) -> None:
        """Guarda fora dos dois lados uma pegged que ainda não tem referência de preço. O(1).

        **A ordem parked mora no índice global, mas em nenhum dos dois lados.** Os dois
        fatos são o mesmo fato visto de dois ângulos. Ela está viva — o cliente pode
        cancelá-la pelo id, e ela volta ao livro assim que surgir uma não-pegged do seu
        lado para lhe servir de referência —, então tem de ser encontrável por ``get``,
        que é por onde ``cancel`` e ``amend`` chegam a qualquer ordem. Mas ela não tem
        preço, e os lados são indexados por preço: não existe nível a que ela pertença, e
        ``BookSide.add`` a recusa justamente por isso.

        A alternativa seria a engine manter as parked num registro próprio, e ela é pior
        de um jeito específico: ``cancel`` consulta o livro, de modo que a ordem parked
        ficaria invisível ao cancelamento e o cliente receberia "ordem não está no livro"
        para uma ordem que ele acabou de enviar e que a engine ainda guarda. A intenção do
        cliente estaria preservada em algum lugar que ele não consegue alcançar. Com o
        registro aqui, o índice global volta a ser o que o nome diz — todas as ordens
        vivas —, e quem pergunta por id não precisa saber em que estado a ordem está.

        As duas guardas são de integridade. Quem decide estacionar uma ordem é a
        reconciliação de peg, não o usuário: estacionar uma ordem que tem preço, ou um id
        que o livro já conhece, é a engine tendo perdido a conta de si mesma.
        """
        if not order.is_parked:
            raise BookIntegrityError(
                f"ordem {order.order_id} não está parked — tem preço, ou não é pegged — e "
                f"não pode ser guardada fora dos dois lados"
            )
        if order.order_id in self._orders or order.order_id in self._parked:
            raise BookIntegrityError(f"order_id {order.order_id} já está no livro")

        self._parked[order.order_id] = order
        self._orders[order.order_id] = order

    def unpark(self, order: Order) -> None:
        """Tira a ordem do registro de parked e do índice global. O(1).

        As duas baixas moram juntas pelo mesmo motivo que as três de ``remove``: separá-las
        é como se produz estado fantasma — aqui, uma ordem no índice global que nem o
        registro de parked nem os dois lados guardam.

        **Não** exige que a ordem ainda esteja *parked*. Quem reativa uma pegged precisa
        lhe dar preço antes de inseri-la no livro, e a ordem em que essas duas coisas
        acontecem é assunto de quem reativa; a guarda pelo registro, comparando identidade,
        já é total sem precisar opinar sobre isso. Retirar do índice global fica a cargo
        deste método porque ``add`` recusa id repetido: a ordem tem de sair antes de voltar.
        """
        if self._parked.get(order.order_id) is not order:
            raise BookIntegrityError(f"ordem {order.order_id} não está parked")

        del self._parked[order.order_id]
        del self._orders[order.order_id]

    @property
    def parked_orders(self) -> tuple[Order, ...]:
        """As ordens parked, da mais antiga para a mais nova. O(K log K).

        A ordem é por ``sequence_id``, e não a de inserção do ``dict``, porque é ela que
        decide a fila: quando surge referência, as parked entram no livro, e quem esperou
        mais tempo entra primeiro. As duas ordens coincidem hoje, o que é exatamente o
        risco — apoiar-se na coincidência daria uma prioridade correta por acidente, que
        deixaria de valer no primeiro caminho que estacione de novo uma ordem já
        estacionada antes, mandando-a para o fim de um dicionário em que ela deveria estar
        na frente.

        Devolve uma tupla, e não o ``dict`` nem uma vista dele, porque quem reconcilia
        reinsere as ordens **enquanto** percorre a lista: iterar sobre a coleção viva
        mutaria o que está sendo iterado.
        """
        return tuple(sorted(self._parked.values(), key=lambda order: order.sequence_id))

    def get(self, order_id: OrderId) -> Order | None:
        """Ordem viva daquele id, ou ``None``. O(1).

        É por este índice que ``cancel`` e ``amend`` chegam à ordem sem varrer o livro, e
        é ele que dá a alça de remoção em O(1) da fila intrusiva. Acha também a ordem
        *parked*, que está viva ainda que fora dos dois lados — ver ``park``.
        """
        return self._orders.get(order_id)

    def __contains__(self, order_id: OrderId) -> bool:
        return order_id in self._orders

    def __len__(self) -> int:
        return len(self._orders)
