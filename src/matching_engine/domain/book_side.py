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

from matching_engine.domain.order import Order
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
    """

    __slots__ = ("_best_index", "_levels", "_side")

    def __init__(self, side: Side) -> None:
        self._side = side
        self._levels: SortedDict[Ticks, PriceLevel] = SortedDict()
        self._best_index = -1 if side is Side.BUY else 0

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
        if level is not None:
            level.add(order)
            return

        level = PriceLevel(order.price)
        level.add(order)
        self._levels[order.price] = level

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
