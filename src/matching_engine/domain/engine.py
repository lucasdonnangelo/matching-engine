"""A engine: a única parte do sistema que decide que duas ordens se cruzam.

O livro guarda, indexa e devolve topos; nada nele sabe que uma compra encontra uma venda.
Essa decisão — contra quem, a que preço, em que quantidade e em que ordem — mora aqui, e
sai como ``list[Event]``, nunca como texto.

Esta etapa cobre a submissão de ordens. Cancelamento, alteração, ordens pegged e
visualização do livro entram depois, sobre a mesma estrutura e sem tocar neste laço.
"""

from __future__ import annotations

from matching_engine.domain.events import BookEntry, BookSnapshot, Event, OrderAccepted, Trade
from matching_engine.domain.order_book import OrderBook
from matching_engine.domain.price import Ticks
from matching_engine.domain.side import Side


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
        """Consome o lado oposto enquanto houver preço aceitável, e repousa o resto se puder.

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
