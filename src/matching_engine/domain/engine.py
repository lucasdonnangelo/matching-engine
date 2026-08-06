"""A engine: a única parte do sistema que decide que duas ordens se cruzam.

O livro guarda, indexa e devolve topos; nada nele sabe que uma compra encontra uma venda.
Essa decisão — contra quem, a que preço, em que quantidade e em que ordem — mora aqui, e
sai como ``list[Event]``, nunca como texto.

Esta etapa cobre submissão, cancelamento, alteração e visualização. Faltam as ordens
pegged, que entram depois sobre a mesma estrutura e sem tocar no laço de matching.
"""

from __future__ import annotations

from matching_engine.domain.events import (
    BookEntry,
    BookSnapshot,
    Event,
    OrderAccepted,
    OrderAmended,
    OrderCancelled,
    Trade,
)
from matching_engine.domain.order import OrderId
from matching_engine.domain.order_book import BookIntegrityError, OrderBook
from matching_engine.domain.price import Ticks
from matching_engine.domain.side import Side


class OrderNotFoundError(ValueError):
    """Comando que se refere a uma ordem que não está viva no livro.

    ``ValueError`` pelo critério da seção 6 do contrato: é o usuário errando o número da
    ordem — digitou um id que nunca existiu, ou um que já saiu do livro —, e a sessão
    continua depois de uma linha de erro.

    ``BookIntegrityError`` é ``RuntimeError`` e significa o oposto: lá o id foi encontrado,
    mas o índice global e os níveis de preço discordam entre si sobre o que o livro tem.
    Um é engano de quem digita; o outro é a engine tendo perdido a conta de si mesma, e por
    isso um vira mensagem e o outro derruba a sessão.
    """


class MatchingEngine:
    """Matching por prioridade preço-tempo sobre um livro próprio.

    Market e limit percorrem **um único** caminho de execução. A market não é outro tipo
    de ordem: é uma limit sem limite de preço, mais a política de descartar o que sobrar.
    Dois caminhos separados duplicariam o mesmo laço — o mesmo ``min(remaining,
    maker.remaining)``, a mesma baixa do maker esgotado, a mesma releitura do topo — e
    duplicata diverge justamente onde ninguém olha: no tratamento da execução parcial,
    que é o caso raro em teste e o comum em produção. Com um caminho só, a superfície de
    teste do matching cai pela metade e toda correção vale para os dois tipos de uma vez.

    O que separa os dois são dois parâmetros de ``_submit``: o limite de preço, que é
    ``None`` na market, e o repouso, que é ``False`` nela. O remanescente de uma market é
    descartado — política IOC —, comportamento inferido dos exemplos do enunciado:
    ``market buy 200`` contra 150 disponíveis executa 150 e os 50 restantes somem, sem
    repousar no livro. Não é detalhe de implementação, é a semântica da ordem: quem manda
    uma market pede execução imediata a qualquer preço, e o que não executou agora não
    tem preço nenhum a que repousar depois.

    A engine é dona do seu ``OrderBook`` e o expõe apenas para leitura. Mutação passa
    pelos métodos daqui, porque é a engine que sabe emitir o evento correspondente: uma
    ordem inserida direto no livro por fora entraria sem ``OrderAccepted``, e o histórico
    de eventos deixaria de explicar o estado do livro.
    """

    __slots__ = ("_book",)

    def __init__(self) -> None:
        self._book = OrderBook()

    @property
    def book(self) -> OrderBook:
        """O livro sobre o qual esta engine opera; é daqui que presenter e testes leem."""
        return self._book

    def submit_limit(self, side: Side, price: Ticks, quantity: int) -> list[Event]:
        """Insere uma limit: executa contra o lado oposto até o limite, o resto repousa.

        Cruzar o spread é execução, não erro — ver ADR 0001.
        """
        return self._submit(side, price, quantity, rest=True)

    def submit_market(self, side: Side, quantity: int) -> list[Event]:
        """Insere uma market: executa a qualquer preço e descarta o que não executar."""
        return self._submit(side, None, quantity, rest=False)

    def cancel(self, order_id: OrderId) -> list[Event]:
        """Retira do livro a ordem viva daquele id. O(1) esperado, O(log P) se o nível esvaziar.

        O evento é montado **antes** da remoção porque depois dela a ordem está desligada:
        ``OrderQueue.remove`` zera os ponteiros de fila, e o que o evento relata é o estado
        do instante em que a ordem ainda estava no livro. Ler os campos depois seria relatar
        o depois de um acontecimento que é justamente o antes.

        Limitação conhecida: uma ordem totalmente executada é **indistinguível** de um id
        que nunca existiu, porque as duas coisas dão a mesma resposta no índice global —
        ausência. Separá-las exigiria um cemitério de ordens mortas, isto é, estado morto
        mantido vivo para melhorar o texto de uma mensagem de erro, com custo de memória
        proporcional ao histórico da sessão. A mensagem enumera as três hipóteses em vez de
        escolher uma que a engine não tem como confirmar.
        """
        order = self._book.get(order_id)
        if order is None:
            raise OrderNotFoundError(
                f"ordem {order_id} não está no livro (id inexistente, já cancelada ou já executada)"
            )

        cancelled = OrderCancelled(
            order_id=order.order_id,
            side=order.side,
            price=order.price,
            remaining=order.remaining,
        )
        self._book.remove(order)
        return [cancelled]

    def amend(
        self, order_id: OrderId, new_price: Ticks | None, new_quantity: int | None
    ) -> list[Event]:
        """Altera preço e/ou quantidade de uma ordem viva, com ou sem custo de prioridade.

        A política está na ADR 0005 e cabe em três linhas: reduzir quantidade mantém o lugar
        na fila, aumentar quantidade renova, mudar preço renova. A fila é recurso escasso —
        quem cresce toma lugar de quem entrou depois e paga tempo por isso, enquanto quem
        encolhe não prejudica ninguém que esteja atrás.

        **A comparação é contra ``quantity``, não contra o remanescente.** Para uma ordem
        parcialmente executada as duas bases não apenas divergem: elas dão respostas
        opostas para o mesmo número. Uma ordem de 100 com 40 já executados tem 60 de saldo,
        e ``qty 80`` é redução perante a quantidade (100 vira 80) e aumento perante o saldo
        (60 viraria 80). A base é a quantidade porque é ela que o cliente enviou e é dela
        que ele se lembra: quem pediu 100 e agora pede 80 está encolhendo o pedido,
        independentemente de quanto do pedido já foi atendido. Lida contra o saldo, a
        política passaria a depender do quanto a ordem executou desde o envio — quantidade
        que o cliente não controla e pode nem conhecer no instante em que digita, de modo
        que o mesmo comando custaria prioridade ou não conforme o mercado tivesse ou não
        atingido a ordem no meio do caminho. O novo saldo é, então, o que resta do pedido
        novo: ``target_quantity - executado``.

        Daí também a recusa de reduzir a ordem para um valor que já foi executado ou menos:
        os negócios fechados não voltam, e uma ordem de 100 com 40 executados não pode
        virar uma ordem de 30. Não é ajuste a arredondar, é pedido impossível.

        **Um amend que torna a ordem marketable executa**, pelo mesmo motivo pelo qual uma
        limit agressiva executa na entrada — ADR 0001. Preço agressivo é preço agressivo,
        tenha vindo de um submit ou de um amend, e é o que o cliente está pedindo ao subir o
        preço de uma compra. Tratar os dois casos de modo diferente abriria um caminho pelo
        qual o livro ficaria cruzado: bastaria repousar uma compra a 9, com uma venda a 10
        no livro, e alterá-la para 11. O item 1 dos invariantes deixaria de valer por
        construção, e passaria a depender de por qual comando a ordem chegou ao preço que
        tem.

        **A ordem sai do livro e volta, em vez de ter o preço mutado no lugar.** O nível é
        indexado por preço e é a autoridade sobre o preço do que guarda: mutar
        ``order.price`` com a ordem enfileirada a deixaria num nível que não é o dela, e ela
        seria anunciada e executada a um preço que não é o seu. Sair também é o que dá ao
        matching um livro coerente — a ordem alterada não disputa contraparte contra um
        livro que ainda a contém — e é o que faz os totais dos dois níveis fecharem sem
        nenhuma correção manual: a remoção baixa o total do nível antigo e a reinserção soma
        no novo, cada uma pela operação que já existe.

        Entre a saída e o retorno é que preço, quantidade e remanescente mudam, e a mudança
        é **pedida** a ``Order.amend_to``, não escrita aqui. A entidade recusa a alteração
        enquanto a ordem estiver ligada a alguma fila, de modo que a janela segura deixa de
        ser promessa desta docstring e passa a ser condição verificada: fora do livro,
        nenhum ``total_quantity`` depende dos três campos, e o preço volta a ser conferido
        pela guarda de ``PriceLevel.add`` na reinserção.

        O que a alteração executou é baixado na própria ordem, com ``fill``, porque a ordem
        alterada é o **taker** desta execução e o executado dela é ``quantity - remaining``
        como em qualquer outra. Chamar ``fill`` sem passar por ``PriceLevel`` é correto
        exatamente aqui e em nenhum outro lugar: não há nível guardando esta ordem, logo não
        há total agregado para dessincronizar.

        A recusa de reduzir abaixo do já executado aparece duas vezes, e não é duplicação: a
        daqui recusa antes de qualquer mutação, com a ordem ainda intacta no livro, e a de
        ``amend_to`` é a autoridade final, que vale para qualquer chamador — inclusive um
        que ainda não exista.

        Alterar para o mesmo preço, sem quantidade nova, é alteração que não altera nada, e
        responde ``OrderAmended`` sem tocar no livro. Não é erro: o cliente pediu que a
        ordem estivesse a 10, e ela está. Recusar obrigaria quem escreve script a conferir
        antes o estado do livro para saber se pode mandar o comando.

        Ordem *parked* — pegged sem referência, fora dos dois lados — não é alterável nesta
        etapa. Ela não tem preço nem nível, de modo que nenhuma das duas políticas se aplica
        a ela sem uma decisão própria, que a etapa de pegged toma.

        Complexidade: O(1) na redução pura — o índice global acha a ordem, o índice de
        preços acha o nível, e o resto é uma subtração — e O(log P + F) quando renova, que é
        o custo de uma submissão, já que é exatamente isso que a renovação faz. Nenhum dos
        caminhos varre o livro.
        """
        if new_price is None and new_quantity is None:
            raise ValueError(
                f"alteração da ordem {order_id} não pede preço nem quantidade: nada a alterar"
            )

        order = self._book.get(order_id)
        if order is None:
            raise OrderNotFoundError(
                f"ordem {order_id} não está no livro (id inexistente, já cancelada ou já executada)"
            )

        current_price = order.price
        if current_price is None:
            raise ValueError(
                f"ordem {order_id} está parked, à espera de referência de peg, e ainda não pode "
                f"ser alterada"
            )

        executed = order.quantity - order.remaining
        if new_quantity is not None and new_quantity <= executed:
            raise ValueError(
                f"ordem {order_id} não pode ser reduzida para {new_quantity}: ela já executou "
                f"{executed} de {order.quantity}"
            )

        target_price = current_price if new_price is None else new_price
        target_quantity = order.quantity if new_quantity is None else new_quantity
        target_remaining = target_quantity - executed
        renews = target_price != current_price or target_quantity > order.quantity

        if not renews:
            # Pedir de novo a quantidade que a ordem já tem não muda nada, e o nível não é
            # tocado: ``reduce`` recusa — com razão — um saldo "novo" igual ao atual.
            if target_remaining < order.remaining:
                level = self._book.side(order.side).level_at(current_price)
                if level is None:
                    raise BookIntegrityError(
                        f"ordem {order_id} está no índice global, mas o lado "
                        f"{order.side.name} não tem nível no preço {current_price}"
                    )
                level.reduce(order, target_remaining)
            return [
                OrderAmended(
                    order_id=order.order_id,
                    side=order.side,
                    price=current_price,
                    quantity=order.remaining,
                    priority_renewed=False,
                )
            ]

        self._book.remove(order)
        order.renew_priority(self._book.next_sequence())
        order.amend_to(target_price, target_quantity)

        events, remaining = self._match(order.side, target_price, order.remaining)
        traded = order.remaining - remaining
        if traded > 0:
            order.fill(traded)

        if not order.is_filled:
            self._book.add(order)
            events.append(
                OrderAmended(
                    order_id=order.order_id,
                    side=order.side,
                    price=target_price,
                    quantity=order.remaining,
                    priority_renewed=True,
                )
            )
        return events

    def snapshot(self) -> list[Event]:
        """Retrato do livro inteiro, dos dois lados, em ordem de prioridade.

        O percurso é O(N), e essa é a única complexidade possível: a saída tem uma linha
        por ordem viva, então produzi-la custa N. É um dos três casos em que a seção 5 do
        contrato admite varredura, e é inerente — não há estrutura de índice que faça uma
        listagem completa custar menos do que o tamanho dela.

        Devolve uma lista de um evento só, e não o evento solto, porque a fronteira trata
        o resultado de todo comando do mesmo jeito: uma sequência de eventos a formatar.
        Um tipo de retorno diferente para este comando obrigaria o ``Cli`` a saber qual
        comando produz lista e qual produz evento, que é conhecimento que ele não deve ter.
        """
        return [BookSnapshot(bids=self._entries(Side.BUY), asks=self._entries(Side.SELL))]

    def _entries(self, side: Side) -> tuple[BookEntry, ...]:
        """Um lado do livro achatado em entradas, do melhor preço para o pior.

        A ordem sai pronta das duas iterações e não é reordenada aqui: ``BookSide`` entrega
        os níveis em ordem de preço e ``PriceLevel`` entrega a fila em FIFO. Ordenar de
        novo neste ponto seria duplicar — e poder contradizer — a prioridade que as duas
        estruturas já mantêm.
        """
        return tuple(
            BookEntry(quantity=order.remaining, price=level.price)
            for level in self._book.side(side)
            for order in level
        )

    def _submit(
        self, side: Side, limit_price: Ticks | None, quantity: int, rest: bool
    ) -> list[Event]:
        """Executa a ordem contra o livro e repousa o remanescente, quando há onde repousar.

        A ordem que repousa é **nova**: id novo, prioridade nova. É o que separa este
        caminho do amend, que executa o mesmo matching mas devolve ao livro a ordem que já
        existia, com o id que o cliente conhece.
        """
        events, remaining = self._match(side, limit_price, quantity)

        # Ordem sem limite de preço é a market, que não repousa: as duas condições nunca
        # coincidem. Conferir isso aqui, em vez de assumir, é o que torna impossível — e
        # não apenas proibido — inserir no livro indexado por preço uma ordem sem preço.
        if remaining > 0 and rest and limit_price is not None:
            order = self._book.create_order(side, limit_price, remaining)
            self._book.add(order)
            events.append(
                OrderAccepted(
                    order_id=order.order_id,
                    side=order.side,
                    price=limit_price,
                    quantity=remaining,
                )
            )

        return events

    def _match(
        self, side: Side, limit_price: Ticks | None, quantity: int
    ) -> tuple[list[Event], int]:
        """Consome o lado oposto enquanto houver preço aceitável; devolve os fills e o que sobrou.

        Separado do repouso porque são os dois chamadores que divergem depois dele, não
        antes: uma submissão que sobra cria ordem no livro, um amend que sobra recoloca a
        ordem existente, e uma market descarta. O matching é o mesmo nos três, e mantê-lo
        num lugar só é o que impede que o caminho do amend divirja do caminho da submissão
        justamente onde ninguém olha — na execução parcial. O que varia fica com quem chama.

        O preço de cada execução é o do **maker**, nunca o do agressor. O maker chegou
        antes, ficou exposto e teve seu preço acordado primeiro; o taker aceitou preço
        igual ou melhor que o seu limite, e quando é melhor a diferença fica com ele —
        price improvement. Concretamente: com um bid de 10 no livro, uma ``limit sell 9``
        executa a **10**, não a 9. Usar o preço do agressor daria ao recém-chegado o poder
        de piorar o preço de quem já estava lá, e a fila deixaria de valer alguma coisa.

        São dois laços. O externo escolhe o nível: lê o topo do lado oposto e para assim
        que o topo deixa de cruzar o limite — como os níveis vêm em ordem de preço, o
        primeiro que não cruza garante que nenhum dos seguintes cruza. O interno consome a
        fila daquele nível pela cabeça, que é a ordem de maior prioridade temporal.

        A baixa do maker esgotado é ``book.remove``, e não ``level.remove``: só a primeira
        fecha as três — a ordem sai do nível, o nível vazio sai do índice de preços e a
        ordem sai do índice global. Isso significa que o nível referenciado pela variável
        local pode ser retirado do índice enquanto o laço interno ainda o segura, e é
        justamente o que se quer: o nível órfão responde ``head`` ``None``, o laço interno
        sai, e o externo relê ``best_level``, que já é o próximo preço. Está correto por
        construção — nenhuma ordem viva fica presa num nível fora do índice —, mas é sutil
        o bastante para merecer a nota.

        Complexidade O(log P + F): cada iteração do laço externo lê um topo em O(log P) e
        só volta a ler quando um nível inteiro se esgota, e cada iteração do interno gasta
        O(1) e ou zera o agressor ou retira definitivamente uma ordem do livro. Nenhuma
        ordem consumida volta, então o custo por fill é amortizado O(1) — o ótimo
        possível, já que F execuções exigem F operações.
        """
        events: list[Event] = []
        remaining = quantity
        opposite = self._book.opposite_side(side)

        while remaining > 0:
            level = opposite.best_level
            if level is None or not self._crosses(limit_price, level.price, side):
                break

            while remaining > 0:
                maker = level.head
                if maker is None:
                    break

                traded = min(remaining, maker.remaining)
                level.fill(maker, traded)
                remaining -= traded
                events.append(
                    Trade(
                        price=level.price,
                        quantity=traded,
                        maker_order_id=maker.order_id,
                        taker_side=side,
                    )
                )
                if maker.is_filled:
                    self._book.remove(maker)

        return events, remaining

    @staticmethod
    def _crosses(limit_price: Ticks | None, resting_price: Ticks, side: Side) -> bool:
        """Diz se uma ordem deste lado, com este limite, aceita executar a ``resting_price``.

        ``None`` é o limite ausente e responde sempre ``True``: quem não impôs limite
        aceita qualquer preço. É aqui que market e limit se unificam — a market entra no
        mesmo laço com o predicado sempre verdadeiro, de modo que ela para por falta de
        liquidez (``best_level`` ``None``) e nunca por preço, enquanto a limit para pelos
        dois motivos. Sem isso, o laço precisaria de um ramo "é market?" a cada nível, que
        é exatamente a bifurcação que a decisão de um caminho só existe para evitar.

        A comparação inverte com o lado porque "melhor" inverte com o lado: o comprador
        aceita executar a preço menor ou igual ao seu limite, o vendedor a preço maior ou
        igual. A igualdade cruza nos dois — ordens ao mesmo preço executam, e é assim que
        o livro deixa de ficar cruzado depois do matching.
        """
        if limit_price is None:
            return True
        if side is Side.BUY:
            return limit_price >= resting_price
        return limit_price <= resting_price
