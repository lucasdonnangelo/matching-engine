"""Ordem: o dado que o livro guarda e a engine executa.

Toda combinação impossível é barrada na construção, não no matching: ``peg bid sell`` e
limit sem preço não existem como objeto. Quem recebe um ``Order`` adiante — nível de
preço, livro, engine — não precisa reverificar nada disso.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NewType

from matching_engine.domain.price import Ticks
from matching_engine.domain.side import PegReference, Side

if TYPE_CHECKING:
    # Só para tipagem: em tempo de execução ``order_queue`` importa daqui, e o import nos
    # dois sentidos seria circular. As anotações são strings por causa do ``__future__``.
    from matching_engine.domain.order_queue import OrderQueue

OrderId = NewType("OrderId", int)


class InvalidOrderError(ValueError):
    """Ordem construída fora dos seus invariantes, ou pedido do cliente que ela não admite."""


class OrderIntegrityError(RuntimeError):
    """Ordem manipulada fora da sua estrutura: execução impossível, ou alteração no lugar errado.

    ``RuntimeError`` pelo critério da seção 6 do contrato, na mesma família de
    ``QueueIntegrityError`` e ``LevelIntegrityError``: descreve a engine tendo perdido a
    conta de si mesma, e não entrada malformada do usuário — comando digitado nunca chega a
    estes erros.

    Não pode **ser** ``QueueIntegrityError``, apesar do parentesco: ``order_queue`` importa
    ``order``, e herdar na direção inversa fecharia um ciclo de import entre os dois
    módulos. A família é a mesma; o lugar é que tem de ser este.
    """


@dataclass(slots=True, eq=False)
class Order:
    """Uma ordem viva, mutável enquanto tiver remanescente.

    ``order_id`` e ``sequence_id`` são campos separados porque respondem a perguntas
    diferentes: o primeiro é *quem* a ordem é, o segundo é *onde* ela está na fila.
    Nascem juntos, mas divergem no amend — alterar preço ou aumentar quantidade custa
    prioridade, então a ordem recebe um novo ``sequence_id`` e vai para o fim da fila,
    enquanto o ``order_id`` permanece, porque é por ele que o cliente segue chamando
    ``cancel`` e ``amend``. Com um campo só, ou o amend quebraria a referência do
    cliente, ou a ordem alterada manteria uma prioridade que já não merece. A
    reprecificação de uma pegged faz o inverso: preserva o ``sequence_id``, porque a
    mudança partiu da engine e não do cliente.

    ``eq=False`` preserva a igualdade por identidade herdada de ``object``, porque a
    ordem é entidade e não value object: duas ordens de campos idênticos são ordens
    diferentes, e a mesma ordem depois de um ``fill`` continua sendo ela. Igualdade
    estrutural inverteria as duas coisas — e com elas todo ``in`` e ``.remove()``, que
    se apoiam em ``__eq__``. Custaria também o hash, já que com ``eq=True`` e
    ``frozen=False`` o dataclass fixa ``__hash__ = None`` e a ordem não poderia entrar
    em ``set`` nem ser chave de ``dict``. E, com os ponteiros de fila intrusiva que
    vêm a seguir, comparar duas ordens percorreria a lista encadeada até o
    ``RecursionError``.

    ``price`` é opcional porque uma ordem pegged sem referência não tem preço nenhum a
    exibir: ela fica *parked* fora do livro até que surja uma ordem não-pegged do seu
    lado, e só então recebe preço e entra na fila. Descartá-la seria perder a intenção
    do cliente; inventar um preço seria mentir sobre o livro. ``None`` é exclusivo
    desse estado — ordem não-pegged sempre tem preço.

    ``remaining`` não é parâmetro do construtor: nasce igual a ``quantity`` e daí em
    diante muda por ``fill``, que é execução, e por ``reduce_to``, que é o cliente
    encolhendo o próprio pedido. Nenhum caminho legítimo cria uma ordem já parcialmente
    executada — o amend muta a ordem existente em vez de construir outra —, então o estado
    inconsistente não é validado, é inconstruível. O amend que renova prioridade reescreve
    preço, quantidade e remanescente de uma vez, e faz isso por ``amend_to``, que só aceita
    a ordem desligada de qualquer fila: nenhum dos campos da entidade é escrito de fora
    dela.

    ``quantity`` sobrevive à execução parcial porque é ele, e não o saldo por executar,
    a base de comparação da política de amend: aumentar quantidade custa prioridade,
    reduzir a mantém, e a convenção compara contra a quantidade da ordem. Para uma ordem
    parcialmente executada as duas bases divergem — pedir 8 numa ordem de 10 com 4 já
    executados é redução perante a quantidade e aumento perante o remanescente —, de
    modo que sem o valor original a regra ficaria indecidível justamente onde mais
    importa.

    ``prev``, ``next`` e ``queue`` fazem da própria ordem o nó da fila do nível de preço,
    em vez de existir um ``Node`` que a embrulhe. A escolha intrusiva custa uma alocação
    por ordem em vez de duas, e faz o índice global ``order_id -> Order`` devolver
    diretamente a alça necessária para remover em O(1) — sem um segundo mapa
    ``order_id -> Node`` para manter em sincronia com o primeiro. O preço são três campos
    estruturais na entidade, alheios ao negócio; a posse dos três é exclusiva da
    ``OrderQueue``, que é o único ponto do sistema autorizado a lê-los ou escrevê-los.

    O terceiro campo é a alça que torna as guardas de integridade da fila **totais** em
    vez de heurísticas: com ``queue``, pertencer é um fato consultável em O(1), e não uma
    inferência a partir do estado de ``prev`` e ``next`` — que não distingue uma ordem
    desta fila de uma ordem no miolo de outra.

    Não há ponteiro para o nível de preço. O cancelamento localiza o nível pelo índice de
    preços em O(log P), que já é a meta de complexidade da operação, então um
    back-pointer não compraria complexidade nenhuma — só criaria referência circular
    entre ``Order`` e ``PriceLevel``, com mais um campo a manter coerente a cada
    reprecificação de pegged.
    """

    order_id: OrderId
    sequence_id: int
    side: Side
    price: Ticks | None
    quantity: int
    remaining: int = field(init=False)
    peg_reference: PegReference | None = None
    prev: Order | None = field(init=False, default=None, repr=False)
    next: Order | None = field(init=False, default=None, repr=False)
    queue: OrderQueue | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise InvalidOrderError(f"quantidade deve ser maior que zero: {self.quantity}")
        self.remaining = self.quantity
        if self.peg_reference is None:
            if self.price is None:
                raise InvalidOrderError("ordem sem preço só é válida se for pegged")
        elif self.peg_reference.side is not self.side:
            raise InvalidOrderError(
                f"peg cruzado: referência {self.peg_reference.name} não acompanha o lado "
                f"{self.side.name}"
            )

    @property
    def is_filled(self) -> bool:
        """Ordem totalmente executada; sai do livro."""
        return self.remaining == 0

    @property
    def is_pegged(self) -> bool:
        """Ordem cujo preço é ditado pelo topo do livro, não pelo cliente."""
        return self.peg_reference is not None

    @property
    def is_parked(self) -> bool:
        """Pegged à espera de referência: viva, mas fora do livro e sem preço."""
        return self.is_pegged and self.price is None

    def fill(self, qty: int) -> None:
        """Consome parte do remanescente.

        As duas guardas são de integridade, e por isso ``OrderIntegrityError`` e não
        ``InvalidOrderError``. O matching sempre executa ``min(remaining,
        incoming_remaining)``, de modo que um over-fill não chega aqui vindo do usuário: é
        aritmeticamente impossível a partir de um comando bem formado, e só pode ser bug da
        engine. O mesmo vale para quantidade não positiva.

        A escolha da família tem consequência visível. O ``Cli`` converte **toda**
        ``ValueError`` numa linha ``Error:`` e segue a sessão — é assim que erro de digitação
        é tratado —, então um over-fill classificado como ``ValueError`` seria apresentado a
        quem digitou como se a culpa fosse dele, e a engine continuaria a operar sobre um
        livro cujas contas já não fecham. A separação da seção 6 do contrato só entrega o
        que promete se as duas famílias forem atribuídas pela **origem** do erro, e não pelo
        lugar onde ele é detectado.

        Falhar alto no ponto do erro também evita que um remanescente negativo se propague
        em silêncio pelos totais do nível de preço.
        """
        if qty <= 0:
            raise OrderIntegrityError(f"execução deve ter quantidade positiva: {qty}")
        if qty > self.remaining:
            raise OrderIntegrityError(
                f"execução de {qty} excede o remanescente de {self.remaining}"
            )
        self.remaining -= qty

    def reduce_to(self, new_remaining: int) -> None:
        """Encolhe o pedido do cliente, sem que nada tenha sido executado por isso.

        A ``quantity`` muda junto com o remanescente, e não só ele: depois de reduzir, a
        ordem **é** de outro tamanho, e não uma ordem do tamanho antigo com parte
        indisponível. A diferença é substantiva porque é contra a ``quantity`` que a
        política de amend compara — reduzir mantém prioridade, aumentar a renova. Mantido o
        valor antigo, uma ordem de 100 reduzida para 80 e depois alterada para 90 ainda
        pareceria redução, e o cliente teria achado o caminho para crescer de graça:
        encolher e voltar, tomando lugar na fila sem pagar tempo por ele. Com a quantidade
        acompanhando, os 90 são aumento perante os 80, e pagam.

        O que já foi executado é preservado, e é isso que a conta ``new_remaining +
        (quantity - remaining)`` diz: uma ordem de 100 com 40 executados, reduzida para 50
        de saldo, vira uma ordem de 90 — os 40 negociados continuam negociados e não voltam
        a ser oferecidos. Zerar não é redução: uma ordem sem saldo é executada ou
        cancelada, e as duas coisas têm caminho próprio.

        Não mexe na fila, de propósito: quem chama é ``PriceLevel.reduce``, e manter a
        posição é o ponto da operação.

        As guardas de faixa são ``OrderIntegrityError`` porque a família se escolhe pela
        **origem** do erro, e não pelo ponto onde ele é detectado. O pedido do cliente foi
        validado camadas acima, em ``MatchingEngine.amend``; quem chega aqui é
        ``PriceLevel.reduce``, que já conferiu a mesma faixa e é código da engine. Um valor
        fora dela neste ponto é a engine passando adiante um número que ela própria deveria
        ter recusado. Hoje as duas guardas são inalcançáveis pelos caminhos existentes, de
        modo que a classificação não muda comportamento nenhum — ela impede que um chamador
        futuro leve bug interno para a linha de erro do usuário, que é onde ele ficaria
        invisível.
        """
        if new_remaining <= 0:
            raise OrderIntegrityError(f"redução deve deixar saldo positivo: {new_remaining}")
        if new_remaining >= self.remaining:
            raise OrderIntegrityError(
                f"redução para {new_remaining} não encolhe o remanescente de {self.remaining}"
            )
        self.quantity = new_remaining + (self.quantity - self.remaining)
        self.remaining = new_remaining

    def amend_to(self, new_price: Ticks, new_quantity: int) -> None:
        """Reescreve preço e tamanho da ordem, preservando o que ela já executou.

        É o caminho do amend que renova prioridade — mudança de preço ou aumento de
        quantidade, ver ADR 0005 —, e é um método da entidade em vez de três atribuições da
        engine pelo mesmo motivo que faz ``remaining`` não ser parâmetro do construtor:
        estado inválido **inconstruível** vale mais do que estado inválido validado.
        Atribuição direta não revalida invariante nenhuma; ela apenas confia em quem
        escreve, e a confiança não é verificável em revisão nem em teste. Com o método, a
        ordem passa a ter uma única porta por onde os três campos mudam juntos, e o que
        entra por ela é conferido.

        A primeira guarda é estrutural: enquanto a ordem estiver ligada a uma fila, o
        ``total_quantity`` do nível que a guarda depende do seu remanescente, e escrevê-lo
        por fora dessincronizaria os dois — o item 3 dos invariantes, quebrado em silêncio.
        A ordem precisa ter saído do livro antes, e com a guarda isso deixa de ser promessa
        de documentação e vira impossibilidade. Reprecificar no lugar seria pior ainda: a
        ordem ficaria num nível que não é o do seu preço.

        Aqui as duas famílias de exceção da seção 6 do contrato convivem, cada uma no seu
        papel, e a diferença entre elas é a origem do erro. A guarda estrutural é
        ``OrderIntegrityError`` porque quem retira a ordem do livro antes de alterá-la é a
        engine, e falhar nisso é bug dela. A recusa de encolher a ordem para aquém do que
        ela já executou é ``InvalidOrderError`` porque quem pede esse número é o cliente:
        os negócios fechados não voltam, uma ordem de 100 com 40 executados não pode virar
        uma de 30, e a resposta correta é uma linha de erro com a sessão continuando.

        O executado sobrevive à alteração — ``quantity - remaining`` é a mesma diferença
        antes e depois —, porque alterar não desfaz negócio: a ordem passa a ser de outro
        tamanho, do qual parte já foi atendida.
        """
        if self.queue is not None:
            raise OrderIntegrityError(
                f"ordem {self.order_id} está enfileirada e não pode ser alterada no lugar"
            )

        executed = self.quantity - self.remaining
        if new_quantity <= executed:
            raise InvalidOrderError(
                f"alteração da ordem {self.order_id} para {new_quantity} não cobre os "
                f"{executed} que ela já executou"
            )

        self.price = new_price
        self.quantity = new_quantity
        self.remaining = new_quantity - executed

    def repeg_to(self, price: Ticks | None) -> None:
        """Reescreve o preço ditado pelo peg; ``None`` manda a ordem para *parked*.

        É a contrapartida de ``amend_to`` para a mudança que **não** parte do cliente. As
        duas reescrevem preço com a ordem fora de qualquer fila, e por isso compartilham a
        guarda estrutural; o que as separa é tudo o resto. ``amend_to`` mexe em quantidade e
        remanescente, e é acompanhada de ``renew_priority`` no chamador, porque o cliente
        pediu outra ordem. Aqui nada além do preço muda: quantidade, remanescente e
        ``sequence_id`` seguem intactos, e é essa preservação que faz da reprecificação o
        cumprimento do contrato que a ordem pegged pediu, em vez de uma alteração a punir.
        Ver ADR 0004.

        ``None`` é um valor legítimo e não um apagamento: é assim que a ordem sai do livro
        sem morrer, à espera de que surja uma ordem não-pegged do seu lado para lhe servir
        de referência. Um método separado para estacionar teria a mesma guarda e o mesmo
        corpo com outro nome — estacionar **é** reprecificar para preço nenhum.
        """
        if self.peg_reference is None:
            raise OrderIntegrityError(
                f"ordem {self.order_id} não é pegged e não tem preço ditado pelo livro"
            )
        if self.queue is not None:
            raise OrderIntegrityError(
                f"ordem {self.order_id} está enfileirada e não pode ser reprecificada no lugar"
            )

        self.price = price

    def renew_priority(self, sequence_id: int) -> None:
        """Troca o número que decide o lugar da ordem na fila.

        É o preço que a ordem paga por alterar preço ou aumentar quantidade — ver ADR
        0005. A fila é recurso escasso: quem cresce está pedindo mais do que a fila já lhe
        havia concedido, e cobrar tempo por isso é o que impede que o aumento passe à
        frente de quem entrou depois.

        O ``order_id`` **não** muda, e é exatamente para isso que os dois campos são
        separados: o cliente continua se referindo à ordem pelo mesmo número em ``cancel``
        e ``modify``. Trocar o id faria o cliente perder a referência da própria ordem por
        tê-la alterado, e a alteração viraria, na prática, um cancelamento com nova ordem.

        Só escreve o número. Reenfileirar é operação da ``OrderQueue``, e o chamador deve
        ter retirado a ordem do livro antes: prioridade nova com a ordem parada no meio da
        fila seria um número que não corresponde a lugar nenhum.
        """
        self.sequence_id = sequence_id
