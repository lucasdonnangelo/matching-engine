"""Nível de preço: a fila FIFO de um preço e a quantidade que ela agrega.

``total_quantity`` é mantido incrementalmente, e não calculado sob demanda, por causa da
assimetria entre leitura e escrita. Recalcular custaria O(N) no nível — varredura que a
seção 5 do contrato proíbe fora dos três casos inerentes, e este não é um deles —,
enquanto manter custa uma soma de ``int`` embutida em ``add``, ``remove``, ``fill`` e
``reduce``, que já são O(1) e que são os únicos pontos por onde o remanescente do nível
pode mudar. A
quantidade agregada é consultada muito mais vezes do que muda: entra na impressão do
livro, na agregação por preço e em qualquer decisão que olhe o topo, ao passo que só
muda quando uma ordem entra, sai ou executa.

O que se compra em tempo se paga em coerência: o total vira estado a manter em dia. Por
isso ele é invariante do livro — seção 4, item 3 —, verificado após cada comando pela
suíte de propriedade, e por isso ``fill`` existe aqui, em vez de o chamador executar a
ordem por fora.
"""

from __future__ import annotations

from collections.abc import Iterator

from matching_engine.domain.order import Order
from matching_engine.domain.order_queue import OrderQueue
from matching_engine.domain.price import Ticks


class LevelIntegrityError(RuntimeError):
    """Ordem colocada num nível que não é o do seu preço.

    ``RuntimeError`` pela mesma razão de ``QueueIntegrityError``: quem roteia a ordem até
    o nível é a engine, não o usuário, então um preço divergente aqui é bug interno e não
    comando malformado.
    """


class PriceLevel:
    """Ordens vivas de um mesmo preço, da mais antiga para a mais nova.

    ``price`` não muda depois da construção porque é a chave pela qual o lado do livro
    indexa este nível: alterá-lo em vigor deixaria o índice apontando um preço para um
    nível de outro. Reprecificar uma ordem é movê-la de nível, nunca mover o nível.

    ``total_quantity`` é somente-leitura de fora pelo mesmo motivo: ele é derivado das
    ordens da fila e só se mantém verdadeiro se as quatro operações que o alteram forem as
    únicas a escrevê-lo.
    """

    __slots__ = ("_price", "_queue", "_total_quantity")

    def __init__(self, price: Ticks) -> None:
        self._price = price
        self._queue = OrderQueue()
        self._total_quantity = 0

    @property
    def price(self) -> Ticks:
        """Preço de todas as ordens deste nível."""
        return self._price

    @property
    def total_quantity(self) -> int:
        """Soma dos ``remaining`` das ordens do nível."""
        return self._total_quantity

    @property
    def head(self) -> Order | None:
        """Ordem de maior prioridade do nível; é contra ela que o matching executa."""
        return self._queue.head

    @property
    def is_empty(self) -> bool:
        """Nível sem ordens; o lado do livro deve retirá-lo do índice de preços."""
        return self._queue.is_empty

    def add(self, order: Order) -> None:
        """Enfileira a ordem no fim do nível. O(1).

        Preço divergente é recusado porque o nível é a autoridade sobre o preço do que
        guarda: quem lê o livro toma o preço do nível e a quantidade da soma, sem olhar
        ordem por ordem. Uma ordem alojada no nível errado seria anunciada e executada a
        um preço que não é o dela. A guarda cobre também a pegged sem referência, cujo
        preço é ``None`` e que só existe *parked*, fora do livro.
        """
        if order.price != self._price:
            raise LevelIntegrityError(
                f"ordem {order.order_id} tem preço {order.price} e não pertence ao nível "
                f"{self._price}"
            )

        self._queue.append(order)
        self._total_quantity += order.remaining

    def remove(self, order: Order) -> None:
        """Retira a ordem do nível. O(1).

        A fila é desfeita antes de o total baixar, e não por gosto: é a guarda de
        ``OrderQueue.remove`` que recusa ordem alheia, e subtrair só depois dela garante
        que uma recusa não deixe o total mentindo sobre um nível que não mudou.

        Subtrai o ``remaining``, não a ``quantity``: o que a ordem ainda ocupa no nível é
        o que sobrou dela, e o que já executou já foi descontado por ``fill``.
        """
        self._queue.remove(order)
        self._total_quantity -= order.remaining

    def fill(self, order: Order, qty: int) -> None:
        """Executa ``qty`` da ordem e baixa o total do nível junto. O(1).

        É o **único** caminho permitido para executar uma ordem que está no livro. Chamar
        ``order.fill`` direto reduziria o remanescente sem tocar em ``total_quantity``, e
        o nível passaria a anunciar quantidade que não existe mais — quebra do item 3 dos
        invariantes, silenciosa até alguém agregar o livro ou tentar executar contra o
        fantasma.

        A ordem **não** sai do nível ao zerar. Removê-la aqui esconderia metade da baixa:
        uma ordem executada também precisa sair do índice global, que é do livro e não do
        nível, e uma remoção implícita e outra explícita é exatamente como se deixa ordem
        fantasma no índice. Quem executa fecha as duas.

        A guarda de pertencimento está aqui porque ``fill`` não herda nenhuma: ``add`` e
        ``remove`` passam pela ``OrderQueue``, que compara a alça e recusa ordem alheia,
        mas ``Order.fill`` não conhece níveis. Sem a checagem,
        ``level_A.fill(ordem_do_level_B, 5)`` executaria em B e debitaria o total de A, e
        os dois passariam a mentir — a mesma classe de corrupção que a alça ``order.queue``
        eliminou no tamanho da fila, agora no total agregado, isto é, no item 3 dos
        invariantes. Com ela, as três operações do nível têm guarda total, sem assimetria.

        A ordem executa antes de o total baixar porque é ``Order.fill`` que valida ``qty``
        contra o remanescente: uma execução recusada não pode deixar rastro no nível.
        """
        if order.queue is not self._queue:
            raise LevelIntegrityError(f"ordem {order.order_id} não pertence ao nível {self._price}")

        order.fill(qty)
        self._total_quantity -= qty

    def reduce(self, order: Order, new_remaining: int) -> None:
        """Encolhe a ordem a pedido do cliente e baixa o total do nível junto. O(1).

        Existe em vez de reusar ``fill`` porque os dois relatam fatos de negócio distintos.
        ``fill`` é execução: houve contraparte, sai um ``Trade``, e a quantidade que sumiu
        do nível apareceu do outro lado de um negócio. Aqui não houve execução nenhuma — o
        cliente reduziu o próprio tamanho, e a quantidade simplesmente deixou de ser
        oferecida. Um método só para os dois casos misturaria os dois eventos num caminho
        comum, e a primeira pergunta de auditoria, quanto desta ordem foi negociado,
        deixaria de ter resposta na estrutura: o executado é ``quantity - remaining``, e a
        redução move os dois lados dessa conta ao mesmo tempo justamente para não a
        contaminar.

        O que os dois têm em comum é a obrigação de manter ``total_quantity`` em dia, e é
        por isso que ambos moram aqui em vez de no chamador. Fosse a redução feita por
        fora, com ``order.reduce_to`` direto, o nível seguiria anunciando quantidade que o
        cliente já retirou — quebra silenciosa do item 3 dos invariantes, exatamente como
        seria chamar ``order.fill`` por fora.

        A posição na fila **não** muda, e é essa a diferença que a política de amend
        registra: reduzir não prejudica ninguém que esteja atrás, então não custa
        prioridade. Ver ADR 0005.

        As duas guardas são de integridade, não de entrada. A de pertencimento é a mesma de
        ``fill``, pelo mesmo motivo — reduzir aqui uma ordem de outro nível debitaria deste
        total uma quantidade que nunca esteve nele. A de faixa recusa saldo não positivo ou
        que não encolhe, e é ``LevelIntegrityError`` porque quem calcula esse número é a
        engine, a partir da quantidade que o cliente pediu: um valor fora da faixa aqui é
        bug interno, e não comando malformado, que a engine já recusou antes de chegar.

        A diferença é medida antes da escrita porque ``reduce_to`` a apaga: depois dela, o
        remanescente antigo não existe em lugar nenhum. E o total só baixa depois de a
        ordem aceitar a redução, como em ``fill`` — recusa não deixa rastro no nível.
        """
        if order.queue is not self._queue:
            raise LevelIntegrityError(f"ordem {order.order_id} não pertence ao nível {self._price}")
        if new_remaining <= 0 or new_remaining >= order.remaining:
            raise LevelIntegrityError(
                f"redução inválida da ordem {order.order_id}: o novo saldo {new_remaining} tem "
                f"de ser positivo e menor que o remanescente de {order.remaining}"
            )

        reduced_by = order.remaining - new_remaining
        order.reduce_to(new_remaining)
        self._total_quantity -= reduced_by

    def __iter__(self) -> Iterator[Order]:
        """Da mais antiga para a mais nova, que é a ordem de prioridade."""
        return iter(self._queue)

    def __len__(self) -> int:
        return len(self._queue)
