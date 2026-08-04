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
