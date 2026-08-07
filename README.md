# Matching Engine

[![CI](https://github.com/lucasdonnangelo/matching-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/lucasdonnangelo/matching-engine/actions/workflows/ci.yml)

Motor de casamento de ordens de ativo único, em memória, com prioridade preço-tempo. Aceita
ordens `limit`, `market` e `pegged`, e as operações de cancelar, alterar e visualizar o
livro, por um REPL de linha de comando. Python 3.12+, sem framework: a única dependência de
runtime é `sortedcontainers`.

---

## Demonstração

Sessão real, produzida por `python -m matching_engine`:

```
>>> limit sell 10.2 100
Order created: sell 100 @ 10.2 1
>>> limit sell 10.3 200
Order created: sell 200 @ 10.3 2
>>> limit buy 10 200
Order created: buy 200 @ 10 3
>>> limit buy 9.99 100
Order created: buy 100 @ 9.99 4
>>> print book
Ordens de Compra | Ordens de Venda
-----------------|----------------
200 @ 10         | 100 @ 10.2
100 @ 9.99       | 200 @ 10.3
>>> limit buy 10.3 250
Trade, price: 10.2, qty: 100
Trade, price: 10.3, qty: 150
>>> print book
Ordens de Compra | Ordens de Venda
-----------------|----------------
200 @ 10         | 50 @ 10.3
100 @ 9.99       |
>>> limit buy 10.4 100
Trade, price: 10.3, qty: 50
Order created: buy 50 @ 10.4 5
>>> cancel order 5
Order cancelled
>>> print book
Ordens de Compra | Ordens de Venda
-----------------|----------------
200 @ 10         |
100 @ 9.99       |
>>> modify order 3 price 9.99
Order amended: buy 200 @ 9.99 3
>>> print book
Ordens de Compra | Ordens de Venda
-----------------|----------------
100 @ 9.99       |
200 @ 9.99       |
>>> peg bid buy 150
Order pegged: buy 150 @ 9.99 6
>>> print book
Ordens de Compra | Ordens de Venda
-----------------|----------------
100 @ 9.99       |
200 @ 9.99       |
150 @ 9.99       |
>>> limit buy 10.1 300
Order created: buy 300 @ 10.1 7
Order pegged: buy 150 @ 10.1 6
>>> print book
Ordens de Compra | Ordens de Venda
-----------------|----------------
150 @ 10.1       |
300 @ 10.1       |
100 @ 9.99       |
200 @ 9.99       |
>>> quit
```

- **`limit buy 10.3 250`** atravessa dois níveis e sai em dois preços: 100 a 10.2 e 150 a
  10.3, este último uma execução parcial da ordem 2. O preço é o do maker — o comprador
  tinha limite de 10.3 e levou 100 a 10.2, que é *price improvement*.
- **`limit buy 10.4 100`** executa os 50 que restavam a 10.3 e repousa os outros 50 a 10.4,
  no mesmo comando: uma limit que cruza o spread é executada, não recusada ([ADR 0001](docs/adr/0001-marketable-limit-orders.md)).
- **`modify order 3 price 9.99`** tira a ordem 3 do topo e a coloca **atrás** dos 100 @ 9.99
  da ordem 4, que chegou depois dela: mudar preço renova a prioridade ([ADR 0005](docs/adr/0005-amend-priority-policy.md)).
- **`limit buy 10.1 300`** melhora o topo do bid e a pegged o acompanha no mesmo comando,
  aparecendo **acima** da limit que causou a mudança — a reprecificação preserva o
  `sequence_id` ([ADR 0004](docs/adr/0004-pegged-order-repricing.md)).

Entrada inválida vira uma linha de erro e a sessão continua:

```
>>> peg bid sell 100
Error: peg cruzado: referência BID não acompanha o lado SELL
>>> limit buy 1.234 10
Error: preço mais preciso que o tick de 0.01: '1.234'
>>> cancel order 99
Error: ordem 99 não está no livro (id inexistente, já cancelada ou já executada)
```

---

## Como rodar

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m matching_engine
```

Linux / macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m matching_engine
```

Verificações — as três rodam na CI, em Python 3.12 e 3.13:

```bash
pytest          # 561 testes
ruff check .    # lint
mypy            # tipos, modo strict
```

A sessão encerra por `quit`, por `exit`, por EOF (Ctrl-D no Unix, Ctrl-Z e Enter no Windows,
ou fim do arquivo com a entrada redirecionada) e por Ctrl+C.

---

## Comandos

```
limit <buy|sell> <preço> <quantidade>          limit buy 10 200
market <buy|sell> <quantidade>                 market sell 150
peg <bid|offer> <buy|sell> <quantidade>        peg bid buy 150
cancel order <id>                              cancel order 3
modify order <id> [price <p>] [qty <q>]        modify order 3 price 9.99
print book                                     print book
quit | exit                                    quit
```

Palavras-chave não diferenciam maiúsculas de minúsculas. `modify` exige ao menos um dos dois
pares, aceita os dois em qualquer ordem, e recusa chave repetida. O peg é apenas homolateral:
`bid` com `buy`, `offer` com `sell`. Preço tem no máximo duas casas decimais; quantidades e
ids são inteiros maiores que zero.

---

## Arquitetura

```
src/matching_engine/
├── domain/          # regras de negócio puras
│   ├── side.py          Side, PegReference
│   ├── price.py         Ticks, parse_price, format_price
│   ├── order.py         Order (dado + ponteiros de fila + remaining)
│   ├── events.py        Trade, OrderAccepted, OrderCancelled, OrderAmended,
│   │                    OrderPegged, BookSnapshot
│   ├── order_queue.py   lista duplamente encadeada intrusiva
│   ├── price_level.py   fila FIFO + total_quantity + non_pegged_count
│   ├── book_side.py     SortedDict[ticks -> PriceLevel], topos, registro de pegged
│   ├── order_book.py    dois BookSide + índice global + registro de parked
│   └── engine.py        matching, cancel, amend, reconciliação de pegged
└── io/              # fronteira com o mundo
    ├── commands.py      DTOs de comando
    ├── parser.py        texto -> Command
    ├── presenter.py     Event -> texto
    └── cli.py           REPL
```

A regra que organiza o mapa: **`domain/` nunca importa de `io/`, nunca imprime, nunca faz
parsing**. A engine devolve `list[Event]`; converter evento em texto é responsabilidade
exclusiva do presenter, e converter texto em tipo do domínio (`"buy"` → `Side.BUY`) é do
parser.

O que isso compra, concretamente:

- **O domínio se testa comparando dataclasses.** Nenhum teste de matching captura stdout nem
  depende de espaçamento, de largura de coluna ou da ordem em que as linhas saem.
- **A agregação de trades exigida pelo enunciado acontece na apresentação.** O domínio emite
  um `Trade` por par maker/taker, que é a granularidade auditável — qual ordem passiva foi
  atingida, a que preço, em que quantidade. O presenter funde trades consecutivos de mesmo
  preço numa linha, como o enunciado exibe. A direção importa: dos dois trades chega-se à
  linha somada, da linha somada não se volta aos dois trades. Fundir no domínio destruiria a
  informação; fundir na saída é uma projeção refeita a cada apresentação.
- **Trocar o REPL por outra interface não toca no núcleo.** O `Cli` recebe a engine pelo
  construtor e os streams por parâmetro, e por isso a sessão inteira se testa com `StringIO`.

---

## Estruturas de dados e complexidade

Três camadas, cada uma dona de uma pergunta ([ADR 0003](docs/adr/0003-book-data-structures.md)).
A **fila do nível** é uma lista duplamente encadeada intrusiva em que a própria `Order` é o
nó, porque a operação que decide é a remoção arbitrária em O(1) — cancelamento e alteração
chegam pelo id e atingem qualquer posição da fila, não só as pontas. O **índice de níveis de
cada lado** é um `SortedDict[Ticks, PriceLevel]`, a única candidata que entrega ao mesmo tempo
lookup por preço em O(1), melhor preço em O(log P) e iteração ordenada, sem varredura. O
**índice global** `dict[OrderId, Order]` fecha o circuito: como a `Order` é o nó, o valor que
ele devolve já é a alça de remoção, sem um segundo mapa a manter em sincronia.

`N` = ordens vivas · `P` = níveis ativos · `F` = fills · `K` = pegged do lado · `M` = ordens
no nível de destino.

| Operação | Custo |
|---|---|
| Inserir limit passiva | O(log P) |
| Inserir limit agressiva / market | O(log P + F), amortizado O(1) por fill |
| Best bid / best offer | O(log P) |
| Cancel | O(1) esperado, + O(log P) se o nível esvaziar |
| Amend (reduz qty) | O(1) |
| Amend (preço ou aumenta qty) | O(log P), + F se a alteração executar |
| Reprecificar pegged | O(K log K) no caso comum; O(K log K + M) quando há intercalação real |
| `print book` | O(N) |
| Espaço | O(N + P) |

O melhor preço é O(log P), por leitura da ponta do índice ordenado. Um cache de ponteiro daria
O(1) e exigiria invalidação correta em toda inserção, remoção e esvaziamento de nível —
superfície de bug permanente, do tipo que devolve um preço plausível em vez de falhar, por um
ganho que não se mede com P na casa das dezenas.

**Sobre os custos lineares, que são três e não são da mesma natureza.** Dois são **inerentes**:
`print book` produz uma linha por ordem viva, e o matching é linear no número de fills. Nenhuma
estrutura de dados faz melhor, porque o custo é o do próprio resultado.

O terceiro é **admitido por escolha**: o merge da reprecificação de pegged. Existe alternativa
mais barata — mandar as pegged reprecificadas para o fim da fila do nível de destino, O(K)
sempre — e ela foi descartada por contradizer o exemplo do enunciado, no qual a pegged
reprecificada aparece acima da limit que provocou a mudança. O linear aqui é o preço de uma
decisão de negócio, não um limite de estrutura de dados, e é cobrado com parcimônia: o termo M
só aparece quando o topo muda por cancelamento, revelando um nível preexistente cujas ordens
são mais antigas que parte das pegged. No caso comum o cursor da fila de destino não avança e o
merge já é O(K).

---

## Decisões de projeto

O enunciado deixa ambiguidades, e cada uma foi resolvida antes do código, com registro em ADR.

**Limit que cruza o spread é executada** — [ADR 0001](docs/adr/0001-marketable-limit-orders.md).
O enunciado permite ignorar ou preencher, desde que a escolha seja justificada. O argumento
decisivo é de engenharia: executar unifica market e limit em **um único** algoritmo de
matching, porque uma market é uma limit sem limite de preço mais a política de descartar o
remanescente. Recusar obrigaria a manter dois caminhos, que divergiriam justamente no
tratamento da execução parcial — o caso raro em teste e comum em produção.

**Preço é inteiro de ticks** — [ADR 0002](docs/adr/0002-price-as-integer-ticks.md). Tick de
0.01, `Ticks = NewType("Ticks", int)`. `float` não representa 0.01 e corrompe ordenação e
agregação em silêncio. `Decimal` foi descartado também: opera sob a precisão do contexto global,
de modo que escalar uma entrada mais longa que isso arredonda sem erro e faz um sub-tick
desaparecer — a mesma falha por outro caminho. Não há aritmética decimal nem de ponto flutuante
em ponto nenhum do sistema.

**Política de amend** — [ADR 0005](docs/adr/0005-amend-priority-policy.md). Reduzir quantidade
mantém a prioridade; aumentar quantidade ou mudar preço renova. A fila é recurso escasso: quem
cresce toma lugar de quem se enfileirou atrás confiando no que o livro anunciava, e paga com
tempo; quem encolhe não prejudica ninguém. A comparação é contra `quantity`, e não contra o
remanescente, porque para uma ordem parcialmente executada as duas bases dão respostas
**opostas** ao mesmo número — numa ordem de 100 com 40 executados, `qty 80` é redução perante a
quantidade e aumento perante o saldo. A base é o que o cliente enviou, e não o quanto o mercado
o atingiu no intervalo entre ler o livro e digitar.

**Reprecificação de pegged** — [ADR 0004](docs/adr/0004-pegged-order-repricing.md). A referência
é o melhor preço entre as ordens **não-pegged** do lado, e não o topo bruto: com o topo bruto,
cada pegged se ancoraria no preço que outra acabou de assumir, e a terminação passaria a depender
do formato do laço. A reprecificação preserva o `sequence_id`, porque a perda de prioridade do
requisito 4 pune a alteração iniciada pelo **cliente**, e esta parte da engine cumprindo o
contrato que a ordem pediu ao ser enviada. A reconciliação é síncrona, ao fim de cada comando,
para os dois lados — Observer dispararia com o livro em estado transitório e tornaria a
reentrância caminho comum; fila de eventos traria assincronia não pedida. Termina em uma
passada, e a prova é curta: a referência é função apenas das não-pegged, a reconciliação move
apenas pegged, logo ela não altera a própria entrada — ponto fixo por construção, não por
convergência. E como o peg é homolateral, o preço assumido é o topo do próprio lado, que pelo
invariante 1 não alcança o lado oposto: a reprecificação nunca dispara matching.

**Market order é IOC.** Inferido dos exemplos do enunciado: `market buy 200` contra 150
disponíveis executa 150 e os 50 restantes somem, sem repousar. Não é detalhe de implementação —
quem manda uma market pede execução imediata a qualquer preço, e o que não executou agora não
tem preço nenhum a que repousar depois.

**O preço do trade é o do maker.** O maker chegou antes, ficou exposto e teve seu preço acordado
primeiro; o taker aceitou preço igual ou melhor que o seu limite, e quando é melhor a diferença
fica com ele. Usar o preço do agressor daria ao recém-chegado o poder de piorar o preço de quem
já estava na fila.

**A agregação de trades acontece na apresentação, não no domínio.** Ver
[Arquitetura](#arquitetura): o domínio precisa da granularidade auditável, a saída do enunciado
mostra uma linha por preço, e só uma das duas direções preserva informação.

---

## Invariantes

A suíte de propriedade verifica, após **cada** comando de sequências geradas aleatoriamente:

1. `best_bid < best_ask` — o livro nunca fica cruzado.
2. Nenhum `PriceLevel` vazio permanece no índice de preços.
3. `level.total_quantity` == soma de `remaining` das ordens do nível.
4. Toda ordem viva está no índice global **e** em exatamente um nível, ou está *parked*.
5. Conservação: Σ executado do agressor == Σ consumido dos passivos.
6. Toda ordem pegged está no melhor preço não-pegged do seu lado, ou *parked*.
7. No máximo um nível contém apenas ordens pegged, e ele é o topo do lado.

As sete aparecem abertas em onze conferências, porque várias se decompõem em fatos que falham
por motivos diferentes e merecem mensagens diferentes — inclusive os fatos estruturais de que
as sete dependem: contador de não-pegged, pertencimento de fila, encadeamento duplo nos dois
sentidos e ordem de chegada dentro do nível.

---

## Testes

561 testes, em três camadas.

- **Unitários** (`tests/unit/`, 549): uma suíte por módulo, viajando no mesmo commit que o
  código que testam. Cobrem caso feliz, entrada inválida, execução parcial e total, livro
  vazio, múltiplos níveis, FIFO dentro do nível, e as guardas de integridade de cada camada.
- **Propriedade** (`tests/property/`, 7 funções, cada uma reexecutada por 100 a 300 exemplos
  do Hypothesis): sequências aleatórias de até 60 comandos, replayadas contra uma engine nova,
  conferindo as onze invariantes após cada comando. A faixa de preços é estreita de propósito —
  preço aleatório num intervalo largo quase nunca colide, e sem colisão o teste não exercita
  FIFO dentro do nível, ciclo de vida de nível nem reprecificação. Quatro propriedades
  adicionais cobrem o presenter, onde a fusão de trades pode perder quantidade sem que a saída
  deixe de parecer plausível.
- **End-to-end golden** (`tests/e2e/`, 5): os exemplos do enunciado reproduzidos byte a byte,
  pelo `Cli` e pelo laço de leitura completo, inclusive os quadros de `print book` — em que um
  espaço invisível no fim de uma linha quebraria o teste sem que a diferença aparecesse na
  tela. Line endings são LF, fixados por `.gitattributes` e pela reconfiguração de stdout.

**As invariantes foram validadas por injeção deliberada de bugs**, porque um teste de
propriedade que nunca falhou não provou que sabe falhar. Três exemplos, cada um pego pela
conferência correspondente:

| Bug injetado | Conferência que pegou |
|---|---|
| `PriceLevel.fill` deixa de baixar `total_quantity` | conservação (invariante 5): *"o lado BUY perdeu 0 de quantidade viva, mas os trades do comando somam 1"* |
| `OrderBook.remove` deixa de apagar a ordem do índice global | invariante 4: *"o índice global tem 1 ordens, mas os níveis e o registro de parked guardam 0"* |
| Reconciliação de pegged roda em um lado só | invariante 6: *"ordem pegged 2 está a 995, e o melhor preço não-pegged do lado SELL é None"* |

---

## Limitações conhecidas

São decisões de escopo, e cada uma tem um custo conhecido.

- **Sem self-trade prevention.** O enunciado não tem conceito de participante: não há
  cliente, conta ou firma a comparar entre duas ordens. Implementar exigiria antes introduzir
  essa identidade em `Order`, no parser e na gramática — mudança de modelo de domínio, não de
  matching.
- **Ordem já executada é indistinguível de id inexistente no cancel.** As duas dão a mesma
  resposta no índice global: ausência. Separá-las exigiria um cemitério de ordens mortas,
  isto é, estado morto mantido vivo, crescendo com a sessão, para melhorar o texto de uma
  mensagem de erro. A mensagem enumera as três hipóteses em vez de escolher uma que a engine
  não tem como confirmar.
- **Peg cruzado é rejeitado.** `peg offer` numa compra assumiria o preço do lado oposto, que
  cruza o spread por definição: seria uma market disfarçada, com o agravante de o remanescente
  repousar e ser reprecificado para o novo topo oposto a cada movimento, executando de novo. É
  um laço de reprecificação e execução que o cliente não pediu, e é a hipótese cuja negação
  sustenta a prova de terminação.
- **Sem persistência, concorrência ou múltiplos ativos.** Premissas explícitas do enunciado. O
  motor é uma estrutura em memória, de thread única, para um ativo. Nada no desenho impede
  qualquer das três; o que existe é a ausência deliberada de camada de indireção especulativa
  para acomodá-las antes de serem pedidas.

---

## Próximos passos

- **Log de auditoria e replay determinístico.** O domínio **já** devolve `list[Event]`, e o
  `sequence_id` já é monotônico e independente de relógio: persistir a sequência de eventos e
  reexecutá-la é extensão natural da fronteira que já existe, não retrabalho. É também o que
  torna reprodutível um estado de livro relatado em produção.
- **Self-trade prevention**, uma vez existindo conceito de participante. A regra em si é um
  predicado no laço de matching; o que falta é o modelo, não o algoritmo.
- **`TimeInForce` explícito (IOC, FOK, GTD).** O motor já trata IOC internamente — é o que a
  market é —, e a política vive em dois parâmetros de `_submit`. Torná-la um campo da ordem é
  promover o que já existe implicitamente, e FOK acrescenta uma passada de verificação antes
  de executar.
- **Price ladder** — array denso indexado por tick — caso latência passasse a importar e a
  faixa de preços fosse fechada. Hoje é rejeitada porque a memória seria proporcional ao
  intervalo de preços, não ao número de ordens ([ADR 0003](docs/adr/0003-book-data-structures.md)).
- **Benchmarks com livro grande**, para transformar a análise de complexidade em medição. Hoje
  os custos estão argumentados e conferidos contra o código, mas não cronometrados.

---

## Uso de ferramentas de IA

O enunciado permite o uso; o candidato é responsável pelo código entregue, e o que segue
descreve como o trabalho foi organizado para que essa responsabilidade fosse exercível.

**Arquitetura e decisões vieram antes do código.** As ambiguidades do enunciado — limit
marketable, representação de preço, política de amend, semântica de pegged — foram resolvidas e
registradas em ADRs antes de existir implementação, com alternativas rejeitadas e o motivo de
cada rejeição. A implementação seguiu em incrementos pequenos, um por commit, cada um revisado
linha a linha antes de entrar. O `CLAUDE.md` é versionado no repositório como contrato de
arquitetura, invariantes e metas de complexidade: é o documento que a ferramenta recebe e
contra o qual o resultado é conferido.

**A evidência de que a revisão foi real está no repositório, nos dois sentidos.** Correções que
ela produziu, cada uma com o rastro onde está registrada. Nem todas têm commit próprio, e isso
é consequência do fluxo: como cada incremento é revisado **antes** de ser commitado, parte das
correções está incorporada ao commit que introduz o código, e não em commits separados de
conserto — o preço de manter o histórico limpo é que ele não exibe o caminho até cada linha.

- **`eq=False` na `Order`** (commit `b9b2aca`, docstring em `domain/order.py`). Igualdade
  estrutural, o padrão de `dataclass`, inverteria a semântica de uma entidade e quebraria de
  forma concreta ao chegarem os ponteiros da lista intrusiva: comparar duas ordens percorreria
  a lista encadeada até o `RecursionError`, e `__hash__ = None` tiraria a ordem de qualquer
  `set` ou `dict`.
- **`re.fullmatch` no lugar de `re.match` com `$`** (`domain/price.py`). Em Python, `$` casa
  também logo antes de um `\n` final, então `"10\n"` passaria a validação — e como `int()`
  ignora whitespace, o preço seria aceito **em silêncio**.
- **A meta de complexidade da reprecificação de pegged foi corrigida pela medição** (commit
  `59f1ce9`, que altera o `CLAUDE.md` junto com o código). Ela havia sido **estimada** como
  "O(1) no caso comum (splice)", supondo um ramo dedicado de splice na fila. A medição mostrou
  que esse ramo economizaria fator constante, e não ordem de complexidade, porque no caso comum
  o laço geral do merge já é O(K) sozinho — o cursor da fila de destino não chega a avançar. O
  ramo dedicado não foi adiante e a meta foi corrigida para O(K). O fator log da meta atual —
  O(K log K), como na tabela acima — entrou depois, em `ad3bc1e`, com a decisão de ordenar as
  pegged defensivamente por `sequence_id` na coleta, em vez de confiar na ordem de inserção do
  registro — ver `BookSide.pegged_orders`.
- **Guarda de pertencimento em `PriceLevel.fill`** (commit `3c4eb6e`). `add` e `remove` herdam
  a guarda da `OrderQueue`, mas `fill` não herda nenhuma, porque `Order.fill` não conhece
  níveis: sem a checagem, `level_A.fill(ordem_do_level_B, 5)` executaria em B e debitaria o
  total de A, e os dois passariam a mentir.
- **Guardas estruturais migradas para `RuntimeError`.** A migração entrou junto do commit do
  amend (`dcdc319`), e não em commit dedicado ao assunto: foi lá que as guardas de `Order.fill`
  deixaram de ser `InvalidOrderError` e passaram a ser `OrderIntegrityError`. O `Cli` converte
  **toda** `ValueError` numa linha `Error:` e segue a sessão, que é o tratamento correto para
  erro de digitação. Um over-fill classificado como `ValueError` seria apresentado a quem
  digitou como se a culpa fosse dele, e a engine continuaria operando sobre um livro cujas
  contas já não fecham. A família da exceção passou a ser escolhida pela **origem** do erro, e
  não pelo lugar onde ele é detectado.

O histórico do git registra a evolução das decisões: metas de complexidade corrigidas quando a
medição contradisse a estimativa — o melhor preço, de O(1) para O(log P) em `9cd4582`, e a
reprecificação de pegged em `59f1ce9` —, e refatorações isoladas em commits próprios,
separadas da funcionalidade que as motivou (`7be0359`, `ad3bc1e`, `a208958`).

---

## ADRs

| ADR | Assunto |
|---|---|
| [0001](docs/adr/0001-marketable-limit-orders.md) | Ordens limit que cruzam o spread são executadas, ao preço do maker |
| [0002](docs/adr/0002-price-as-integer-ticks.md) | Preço como inteiro de ticks; `float` e `Decimal` rejeitados |
| [0003](docs/adr/0003-book-data-structures.md) | As três camadas do livro, a conferência das metas de complexidade e as estruturas rejeitadas |
| [0004](docs/adr/0004-pegged-order-repricing.md) | Referência, prioridade, gatilho e ausência de referência das ordens pegged, com a prova de terminação |
| [0005](docs/adr/0005-amend-priority-policy.md) | Quando a alteração custa prioridade, e contra qual quantidade a comparação é feita |
