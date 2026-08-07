"""Um lado do livro: os níveis de preço daquele lado, indexados e ordenados.

O lado precisa de três coisas ao mesmo tempo — achar o nível de um preço, achar o melhor
preço e percorrer os níveis em ordem — e as alternativas óbvias entregam duas:

- Heap de preços dá o melhor em O(1), mas não sabe remover um nível arbitrário nem
  iterar em ordem sem se desfazer no caminho. E cancelamento esvazia nível do meio o
  tempo todo, não só do topo.
- ``dict`` simples dá lookup O(1) por preço, mas achar o melhor vira varredura O(P), que
  a seção 5 do contrato proíbe.
- Lista ordenada dá o melhor de imediato e itera de graça, mas cada preço novo desloca a
  cauda na inserção, O(P).

``SortedDict`` entrega as três: lookup O(1) por preço, melhor preço em O(log P) e
iteração ordenada em O(P), sem varredura em nenhuma delas.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from sortedcontainers import SortedDict

from matching_engine.domain.order import Order, OrderId
from matching_engine.domain.price import Ticks
from matching_engine.domain.price_level import LevelIntegrityError, PriceLevel
from matching_engine.domain.side import Side


class BookSide:
    """Os níveis de preço de um lado do livro, indexados por preço e sempre ordenados.

    A chave é o preço em ticks, crescente, nos **dois** lados. Negá-la no lado comprador
    para que a ordenação natural já entregasse o topo economizaria uma linha e cobraria
    juros para sempre: o índice passaria a guardar ``-1000`` para o preço 10, e todo log,
    toda sessão de depuração e toda leitura de código exigiriam a conversão mental de
    volta — com a chance de alguém esquecer o sinal justamente onde o livro cruza.

    O que difere entre os lados é apenas de qual ponta se lê o topo: o melhor bid é o
    maior preço, a melhor offer é o menor. Isso é um índice de posição fixado na
    construção — ``-1`` (último) para ``BUY``, ``0`` (primeiro) para ``SELL`` — passado ao
    ``peekitem``, em vez de um ramo condicional repetido a cada consulta.

    ``_second_index`` é o mesmo raciocínio um passo para dentro: o nível logo atrás do
    topo, ``-2`` no ``BUY`` e ``1`` no ``SELL``. É tudo de que ``best_non_pegged_price``
    precisa, e fixá-lo aqui evita repetir a inversão de lado dentro da consulta.

    ``_pegged`` é o registro das ordens pegged **deste lado que estão no livro**; ver
    ``pegged_orders``.
    """

    __slots__ = ("_best_index", "_levels", "_pegged", "_second_index", "_side")

    def __init__(self, side: Side) -> None:
        self._side = side
        self._levels: SortedDict[Ticks, PriceLevel] = SortedDict()
        self._pegged: dict[OrderId, Order] = {}
        self._best_index = -1 if side is Side.BUY else 0
        self._second_index = -2 if side is Side.BUY else 1

    @property
    def side(self) -> Side:
        """Lado do livro que estes níveis compõem."""
        return self._side

    @property
    def best_price(self) -> Ticks | None:
        """Topo do lado; ``None`` se não há nível nenhum.

        É O(log P), não O(1), e fica registrado como tal. Um ponteiro para o melhor nível
        daria O(1), mas teria de ser invalidado corretamente em toda inserção, remoção e
        esvaziamento de nível — superfície de bug permanente em troca de um ganho que,
        com P na casa das dezenas, não se mede. Sub-linear já cumpre a meta da seção 5.
        """
        level = self.best_level
        return None if level is None else level.price

    @property
    def best_level(self) -> PriceLevel | None:
        """Nível do topo do lado; é contra ele que uma ordem agressiva executa primeiro."""
        if not self._levels:
            return None
        best: tuple[Ticks, PriceLevel] = self._levels.peekitem(self._best_index)
        return best[1]

    @property
    def best_non_pegged_price(self) -> Ticks | None:
        """Melhor preço entre os níveis que guardam alguma ordem não-pegged. O(log P).

        É a referência de preço das ordens pegged deste lado — decisão da seção 3 do
        contrato: uma pegged se ancora no melhor preço entre as ordens **não**-pegged, e
        não no topo bruto. Tomar o topo bruto criaria dependência circular entre pegged, já
        que cada uma passaria a se ancorar no preço que a outra acabou de assumir, e a
        reprecificação deixaria de terminar numa passada.

        **Dois níveis bastam**, e é o item 7 dos invariantes que garante isso. Todas as
        pegged de um lado tomam o mesmo preço — esta própria resposta —, então elas se
        acumulam num único nível, que ou já contém as não-pegged que lhes servem de
        referência, ou fica exatamente um preço à frente delas, no topo. Daí os dois casos:
        se o topo tem alguma não-pegged, ele é a resposta; se não tem, ele é o nível
        pegged-only, e o nível logo atrás é obrigatoriamente o das não-pegged de onde o
        preço das pegged veio. Duas leituras de ponta, e a consulta continua O(log P).

        Um segundo nível também sem não-pegged **não** é caso a tratar: é o item 7 violado,
        e a resposta é ``LevelIntegrityError``. Continuar descendo o lado até achar alguma
        não-pegged devolveria um preço plausível e faria duas coisas ruins ao mesmo tempo —
        mascararia a violação, deixando o livro operar corrompido até que o sintoma
        aparecesse longe da causa, e transformaria uma consulta O(log P) na varredura O(P)
        que a seção 5 do contrato proíbe. O erro é ``RuntimeError`` pelo critério da seção
        6: quem coloca ordem pegged em nível é a engine, então esse estado é bug interno e
        não comando malformado.

        Lado vazio responde ``None``, e um lado cujo único nível é pegged-only também: não
        há violação nenhuma aí, apenas não existe referência, e é ``None`` que diz isso.
        Quem pergunta é a reconciliação de peg, para a qual ausência de referência não é
        erro — é o que manda a ordem para *parked*.
        """
        if not self._levels:
            return None

        best: tuple[Ticks, PriceLevel] = self._levels.peekitem(self._best_index)
        if best[1].non_pegged_count > 0:
            return best[0]
        if len(self._levels) == 1:
            return None

        second: tuple[Ticks, PriceLevel] = self._levels.peekitem(self._second_index)
        if second[1].non_pegged_count == 0:
            raise LevelIntegrityError(
                f"lado {self._side.name} tem dois níveis seguidos sem ordem não-pegged, "
                f"{best[0]} e {second[0]}: o item 7 dos invariantes foi violado"
            )
        return second[0]

    @property
    def pegged_orders(self) -> tuple[Order, ...]:
        """As ordens pegged deste lado que estão no livro, da mais antiga para a mais nova.

        Existe porque a reconciliação precisa alcançá-las sem varrer nível nenhum, e nenhuma
        outra estrutura daqui entrega isso. As pegged de um lado repousam todas no mesmo
        preço — é o que o item 7 dos invariantes afirma —, mas o nível em que elas estão
        divide espaço com as não-pegged que lhes deram a referência, de modo que separá-las
        custaria O(M) na fila daquele nível. Com M podendo ser muito maior que K, a meta da
        seção 5 do contrato — O(K) no caso comum — deixaria de valer justamente no caso
        comum, que é o da limit nova melhorando o topo.

        Só as que estão no livro. As *parked* não têm preço, logo não estão em lado nenhum, e
        são registradas por ``OrderBook.park``; quem reconcilia junta as duas listas.

        A ordenação é por ``sequence_id``, e não a de inserção do ``dict``, e o custo disso é
        O(K log K) — a única parte da reconciliação que não é linear em K. O fator log é
        deliberado. A ordem de inserção também daria a fila certa hoje, mas só por uma cadeia
        de garantias distantes: o ``sequence_id`` é monotônico, uma pegged nunca o renova, e
        a ordem de inserção sobrevive ao ciclo *park*/*unpark* atravessando três coleções
        diferentes. Basta um elo mudar para a fila sair errada **em silêncio**, porque
        ``merge_ordered`` só confere a ordenação do bloco que recebe, e não a posição dele
        diante do que já estava no nível.

        O ``sorted`` não depende de nenhuma dessas garantias: ele lê a sequência de cada
        ordem e é correto por si. É independência de invariante distante comprada por um
        fator logarítmico sobre um K pequeno — troca que se faz sem hesitar quando a
        alternativa erra calada.

        Devolve tupla, e não o ``dict`` nem uma vista dele, porque quem reconcilia retira e
        reinsere as ordens **enquanto** percorre a lista.
        """
        return tuple(sorted(self._pegged.values(), key=lambda order: order.sequence_id))

    @property
    def is_empty(self) -> bool:
        """Lado sem nível algum — o que não é o mesmo que lado sem ordens.

        Um nível esvaziado só sai do índice quando o chamador pede; ver ``remove_if_empty``.
        """
        return not self._levels

    def level_at(self, price: Ticks) -> PriceLevel | None:
        """Nível daquele preço, ou ``None``. Consulta pura: não cria nada. O(1)."""
        level: PriceLevel | None = self._levels.get(price)
        return level

    def level_for(self, price: Ticks) -> PriceLevel:
        """Nível daquele preço, criando-o se ainda não existir. O(log P) quando cria."""
        level = self.level_at(price)
        if level is None:
            level = PriceLevel(price)
            self._levels[price] = level
        return level

    def add(self, order: Order) -> None:
        """Roteia a ordem para o nível do seu preço, criando o nível se preciso.

        A lateralidade é conferida primeiro porque nada mais adiante confere: uma ordem
        de venda roteada para o lado comprador criaria nível e entraria na fila sem
        nenhuma recusa, e o livro passaria a oferecer compra do que alguém quis vender.

        Ordem *parked* é recusada porque ela não tem preço — é justamente essa a
        definição do estado — e o livro é indexado por preço. Ela existe fora dos dois
        lados, guardada pela engine até surgir referência de peg.

        Um nível recém-criado só entra no índice depois de a ordem ser aceita. Inseri-lo
        antes deixaria um nível vazio para trás se a fila recusasse a ordem, e nível vazio
        no índice é a quebra do item 2 dos invariantes: ele apareceria no topo do lado,
        anunciando preço sem quantidade nenhuma por trás.

        O registro de pegged é atualizado por último, e é aqui que ele fica total: ``add`` e
        ``remove`` são as duas únicas portas por onde uma ordem entra e sai de um lado, então
        o registro não tem como divergir do livro sem que uma delas seja contornada.
        """
        if order.side is not self._side:
            raise LevelIntegrityError(
                f"ordem {order.order_id} é do lado {order.side.name} e não do lado "
                f"{self._side.name}"
            )
        if order.price is None:
            raise LevelIntegrityError(
                f"ordem {order.order_id} está parked, sem preço, e não pertence ao livro"
            )

        level = self.level_at(order.price)
        if level is None:
            level = PriceLevel(order.price)
            level.add(order)
            self._levels[order.price] = level
        else:
            level.add(order)

        if order.is_pegged:
            self._pegged[order.order_id] = order

    def merge_ordered(self, orders: list[Order]) -> None:
        """Insere um bloco de mesmo preço, já ordenado por ``sequence_id``. O(K + M).

        É por aqui que a reprecificação de pegged devolve o bloco ao livro, e o que a separa
        de ``add`` chamada K vezes é a **posição**: ``add`` enfileira pelo fim, e uma pegged
        reprecificada mantém o ``sequence_id`` original — ver ADR 0004 e
        ``OrderQueue.merge_ordered``. Enfileirada pelo fim, ela apareceria abaixo da limit
        que provocou a mudança de preço, e não acima, contradizendo o exemplo do enunciado.

        O bloco inteiro é conferido antes de qualquer inserção, inclusive quanto ao preço
        comum: ele vai para **um** nível, e uma ordem de preço divergente no meio da lista
        seria alojada num nível que não é o dela. Como em ``add``, um nível recém-criado só
        entra no índice depois de o bloco ser aceito, para que uma recusa não deixe nível
        vazio para trás.
        """
        if not orders:
            return

        price = orders[0].price
        if price is None:
            raise LevelIntegrityError(
                f"ordem {orders[0].order_id} está parked, sem preço, e não pertence ao livro"
            )
        for order in orders:
            if order.side is not self._side:
                raise LevelIntegrityError(
                    f"ordem {order.order_id} é do lado {order.side.name} e não do lado "
                    f"{self._side.name}"
                )
            if order.price != price:
                raise LevelIntegrityError(
                    f"bloco com mais de um preço: a ordem {order.order_id} está a "
                    f"{order.price} e o bloco vai para o nível {price}"
                )

        level = self.level_at(price)
        if level is None:
            level = PriceLevel(price)
            level.merge_ordered(orders)
            self._levels[price] = level
        else:
            level.merge_ordered(orders)

        for order in orders:
            if order.is_pegged:
                self._pegged[order.order_id] = order

    def remove(self, order: Order) -> None:
        """Retira a ordem do seu nível. O(1) esperado.

        O nível esvaziado **permanece** no índice; tirá-lo é chamada à parte, ver
        ``remove_if_empty``.

        A lateralidade vem antes da busca do nível porque sem ela a ordem do lado errado
        só falharia por acidente — quando aquele preço não tivesse nível deste lado.
        Havendo nível, e num livro ativo os preços dos dois lados se aproximam, a chamada
        desceria até ``PriceLevel.remove``, que a recusa pela alça ``order.queue``: erro
        certo, motivo errado, apontando fila quando o problema é lado. Cada camada valida
        aquilo de que é autoridade — o lado sobre lateralidade, o nível sobre preço, a
        fila sobre pertencimento.
        """
        if order.side is not self._side:
            raise LevelIntegrityError(
                f"ordem {order.order_id} é do lado {order.side.name} e não do lado "
                f"{self._side.name}"
            )

        level = None if order.price is None else self.level_at(order.price)
        if level is None:
            raise LevelIntegrityError(
                f"ordem {order.order_id} não está em nenhum nível do lado {self._side.name}"
            )
        level.remove(order)
        # Depois da fila, e não antes: a guarda de pertencimento de ``OrderQueue.remove`` é
        # que recusa ordem alheia, e uma recusa não pode esvaziar o registro de pegged.
        if order.is_pegged:
            del self._pegged[order.order_id]

    def remove_if_empty(self, level: PriceLevel) -> None:
        """Tira o nível do índice se ele estiver vazio. O(log P).

        A baixa é explícita, e não automática dentro de ``remove``, porque ela não é a
        única baixa da operação: quem retira uma ordem do livro precisa retirá-la também
        do índice global de ordens, que pertence ao ``OrderBook`` e não ao lado. Uma baixa
        implícita ao lado de uma explícita é exatamente como se deixa estado fantasma —
        então o item 2 dos invariantes é responsabilidade visível de quem chama, e não uma
        consequência que se descobre lendo a implementação.

        A comparação é por identidade, não por preço: o índice só perde o nível que ele
        próprio guarda naquele preço. Isso torna a chamada repetida inócua e impede que um
        nível homônimo vindo de fora derrube o nível vivo.
        """
        if level.is_empty and self.level_at(level.price) is level:
            del self._levels[level.price]

    def __iter__(self) -> Iterator[PriceLevel]:
        """Da melhor para a pior prioridade de preço: decrescente no bid, crescente na offer.

        É a ordem em que o livro é impresso e em que o matching consome os níveis.
        """
        levels: Sequence[PriceLevel] = self._levels.values()
        return reversed(levels) if self._side is Side.BUY else iter(levels)

    def __len__(self) -> int:
        return len(self._levels)
