"""Fila FIFO de um nível de preço: lista duplamente encadeada intrusiva sobre ``Order``.

A operação que decide a estrutura é a **remoção arbitrária**. Cancelamento e alteração
atingem qualquer posição da fila, não apenas as pontas, e têm meta de O(1). Um ``deque``
só é O(1) nas extremidades: remover um elemento do meio custa a varredura que o encontra,
O(N). Uma ``list`` faz a mesma varredura e ainda desloca a cauda. E nenhum dos dois
oferece alça estável para um elemento — índices mudam a cada remoção, então guardar
"posição 7" não serve de referência.

Com a própria ``Order`` sendo o nó, remover é cirurgia de ponteiros sem busca alguma: o
índice global entrega a ordem, a ordem já carrega os vizinhos, e religar a fila é uma
atribuição para cada lado. O encadeamento é duplo, e não simples, exatamente por causa
desse religamento — desligar um nó exige reescrever o ``next`` do anterior, e numa lista
simplesmente encadeada achar esse anterior custaria de novo a varredura que se quer
evitar.

O que se abre mão é do acesso por índice, que o matching não usa: ele consome sempre pela
cabeça, que é a ordem de maior prioridade.
"""

from __future__ import annotations

from collections.abc import Iterator

from matching_engine.domain.order import Order


class QueueIntegrityError(RuntimeError):
    """Encadeamento inconsistente: ordem já ligada, ou alheia à fila que a manipula.

    Não é ``ValueError`` como ``InvalidOrderError`` porque não descreve entrada inválida
    do usuário — comando malformado nunca chega até aqui. Chegar a este erro significa
    que a engine perdeu o controle de onde suas ordens estão, e a alternativa a falhar
    alto seria uma fila silenciosamente corrompida, com ordens invisíveis ao matching ou
    executadas duas vezes.
    """


class OrderQueue:
    """Ordens de um nível de preço, da mais antiga para a mais nova.

    A fila é dona exclusiva de ``order.prev``, ``order.next`` e ``order.queue``. As
    guardas de integridade consultam o terceiro: pertencer é um fato registrado, não uma
    inferência.

    A alternativa era inferir pertencimento do estado dos ponteiros — ``prev`` nulo só na
    cabeça, ``next`` nulo só na cauda. Também é O(1), mas é parcial: uma ordem no miolo de
    OUTRA fila tem os dois preenchidos e passa. E o dano não pararia na não-detecção — a
    remoção prosseguiria, desligaria a ordem da fila alheia e decrementaria o contador
    desta, levando ``_size`` a valor negativo e fazendo ``is_empty`` responder ``False``
    para uma fila vazia. Como é ``is_empty`` que decide se um nível de preço sai do índice
    de preços, a corrupção sairia da fila e viraria nível fantasma no livro. A alça torna
    a guarda total e, de quebra, dispensa o raciocínio sutil que a outra exigia.
    """

    __slots__ = ("_head", "_size", "_tail")

    def __init__(self) -> None:
        self._head: Order | None = None
        self._tail: Order | None = None
        self._size: int = 0

    @property
    def head(self) -> Order | None:
        """Primeira da fila, de maior prioridade temporal; ``None`` se a fila está vazia.

        É por aqui que o matching consome: ele olha a cabeça, executa contra ela e a
        remove quando esgota. Peek e remoção são passos separados porque uma execução
        parcial deixa a ordem onde está, na frente da fila.
        """
        return self._head

    @property
    def is_empty(self) -> bool:
        """Fila sem ordens; o nível de preço que a contém deve sair do índice."""
        return self._size == 0

    def append(self, order: Order) -> None:
        """Enfileira no fim, a posição de menor prioridade. O(1).

        Só entra ordem solta — nem desta fila, nem de outra. Sem a guarda, reinserir uma
        ordem já enfileirada sobrescreveria seu ``prev`` e partiria a lista em duas, com
        a parte anterior à ordem perdida para sempre.
        """
        if order.queue is not None:
            raise QueueIntegrityError(f"ordem {order.order_id} já está ligada a uma fila")

        order.queue = self
        order.prev = self._tail
        if self._tail is None:
            self._head = order
        else:
            self._tail.next = order
        self._tail = order
        self._size += 1

    def merge_ordered(self, orders: list[Order]) -> None:
        """Insere um bloco já ordenado por ``sequence_id``, mantendo a fila ordenada. O(K + M).

        É a operação de que a reprecificação de pegged precisa e que ``append`` não faz. Uma
        pegged reprecificada **mantém** o ``sequence_id`` original — decisão da seção 3 do
        contrato, porque a mudança de preço partiu da engine e não do cliente, e a perda de
        prioridade do requisito 4 pune alteração pedida pelo cliente. Só que o nível de
        destino tem a sua própria fila, e enfileirar pelo fim colocaria uma ordem antiga
        atrás de ordens mais novas: a fila deixaria de estar ordenada por ``sequence_id``,
        que é o único critério de prioridade temporal que o livro tem, e o desempate entre
        duas ordens do mesmo preço passaria a depender de por qual caminho cada uma chegou
        ao nível. Daí a inserção que já entra no lugar certo.

        **A alternativa era mandar as pegged para o fim da fila**, O(K) sempre, e dispensaria
        este método inteiro. Ela contradiz o exemplo do enunciado: lá a pegged reprecificada
        aparece **acima** da limit que provocou a mudança de preço, e não abaixo dela. E faz
        sentido que apareça — a pegged já estava no livro quando a limit chegou, e
        reprecificar não é reenviar.

        **O caso comum já sai barato do laço geral, e é por isso que não há um ramo para
        ele.** Quando o topo do lado melhora porque chegou uma limit nova, o nível de destino
        é o dela, e todas as pegged — que estavam no livro antes — são mais antigas do que
        qualquer ordem que ele guarde. Nessa forma dos dados a comparação com o cursor falha
        já na primeira tentativa de cada ordem do bloco, o cursor não anda uma casa sequer, e
        cada pegged entra em O(1) logo atrás da anterior: **O(K), independente do tamanho da
        fila que recebe**. Não é otimização a acrescentar, é o que este laço faz sozinho — o
        que um ramo dedicado compraria é fator constante, ao preço de um segundo caminho
        para testar e manter.

        **O M só aparece quando o topo muda por cancelamento.** Aí o preço que as pegged
        passam a acompanhar não é o de uma ordem recém-chegada, é o de um nível que já estava
        no livro, escondido atrás do que foi cancelado, e as ordens dele podem ser mais
        antigas do que parte das pegged. É a intercalação de verdade: o cursor percorre a
        fila de destino uma vez, e só aí se paga O(K + M).

        **É o único custo linear não inerente do sistema.** Dos três casos que a seção 5
        admite, os outros dois não têm como custar menos: imprimir o livro tem uma linha por
        ordem viva, e o matching tem uma operação por execução. Este tem — bastaria abrir mão
        da prioridade preservada da pegged. O linear aqui é o preço de uma decisão de
        negócio, não uma limitação de estrutura de dados, e por isso fica confinado a um
        método só, cobrado apenas quando o topo muda por cancelamento.

        A correção depende de a fila **já** estar ordenada por ``sequence_id``, e ela está:
        ``append`` enfileira em FIFO sobre um contador monotônico, o amend que renova
        prioridade recebe um número novo e maior antes de voltar pelo fim, e este método
        preserva a ordem que encontra. Conferir isso a cada chamada custaria O(M) mesmo
        quando o cursor não sai do lugar, isto é, cobraria o M justamente no caso em que o
        algoritmo não precisa dele.

        As duas guardas cobrem a lista inteira **antes** de qualquer religação, e não ordem a
        ordem durante a inserção: um bloco recusado no meio deixaria parte das ordens já
        enfileiradas e a outra parte solta, com a fila ordenada por acidente ou não conforme
        onde a recusa caísse. Quem monta a lista é a engine, então lista fora de ordem ou com
        ordem ainda ligada a outra fila é bug dela, não comando malformado.
        """
        if not orders:
            return

        previous: Order | None = None
        for order in orders:
            if order.queue is not None:
                raise QueueIntegrityError(f"ordem {order.order_id} já está ligada a uma fila")
            if previous is not None and order.sequence_id <= previous.sequence_id:
                raise QueueIntegrityError(
                    f"bloco fora de ordem: a ordem {order.order_id}, de sequência "
                    f"{order.sequence_id}, vem depois da ordem {previous.order_id}, de "
                    f"sequência {previous.sequence_id}"
                )
            previous = order

        current: Order | None = self._head
        for order in orders:
            while current is not None and current.sequence_id < order.sequence_id:
                current = current.next
            if current is None:
                # A fila acabou antes do bloco: daqui para a frente todas as ordens são as
                # mais novas de tudo, e ``append`` já é exatamente essa inserção.
                self.append(order)
            else:
                self._link_before(order, current)

    def _link_before(self, order: Order, node: Order) -> None:
        """Insere ``order`` imediatamente antes de ``node``, que já está nesta fila. O(1).

        Nunca mexe em ``_tail``: ``node`` continua na fila e depois de ``order``, então a
        cauda é a mesma. O caso em que a cauda muda é o da inserção no fim, e ele é de
        ``append``.
        """
        previous = node.prev
        order.queue = self
        order.prev = previous
        order.next = node
        node.prev = order
        if previous is None:
            self._head = order
        else:
            previous.next = order
        self._size += 1

    def remove(self, order: Order) -> None:
        """Desliga a ordem e religa seus vizinhos entre si. O(1).

        Os três campos de ligação são zerados na saída, e não por higiene: a ordem que
        saiu tem de voltar a ser indistinguível de uma nunca enfileirada, senão a guarda
        de ``append`` a recusaria para sempre e os vizinhos de uma vida anterior seriam
        costurados de volta no reenfileiramento do amend.
        """
        if order.queue is not self:
            raise QueueIntegrityError(f"ordem {order.order_id} não pertence a esta fila")

        if order.prev is None:
            self._head = order.next
        else:
            order.prev.next = order.next

        if order.next is None:
            self._tail = order.prev
        else:
            order.next.prev = order.prev

        order.queue = None
        order.prev = None
        order.next = None
        self._size -= 1

    def __iter__(self) -> Iterator[Order]:
        """Da cabeça para a cauda, que é a ordem de prioridade.

        Remover durante a iteração não é suportado: zerar os ponteiros da ordem corrente
        interrompe o percurso. O matching não esbarra nisso porque consome pela ``head``,
        nunca iterando.
        """
        current = self._head
        while current is not None:
            yield current
            current = current.next

    def __len__(self) -> int:
        return self._size
