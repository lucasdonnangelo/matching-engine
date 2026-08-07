# ADR 0003 — Estruturas de dados do livro

## Contexto

O livro tem de sustentar quatro operações **ao mesmo tempo**, e nenhuma delas é rara:

1. **Inserir uma ordem mantendo prioridade preço-tempo.** É o caminho de toda limit que
   repousa. A ordem entra no nível do seu preço e no fim da fila daquele nível, porque
   dentro de um preço quem chegou antes executa antes.
2. **Remover uma ordem arbitrária em O(1).** Cancelamento e alteração chegam pelo
   `order_id` e atingem **qualquer** posição da fila, não só as pontas. Uma execução
   parcial deixa a ordem na cabeça; um cancelamento tira a terceira de um nível do meio do
   livro. É a operação mais exigente das quatro, e é ela que decide a estrutura da fila.
3. **Achar o melhor preço de cada lado.** Todo matching começa por aí, e a referência de
   preço das ordens pegged também — ver ADR 0004.
4. **Percorrer os níveis em ordem.** `print book` imprime do topo para baixo em cada lado,
   e o matching consome os níveis exatamente nessa ordem.

A dificuldade não está em nenhuma delas isolada. Está em tê-las juntas: cada estrutura
óbvia entrega duas ou três e cobra a quarta em O(N) ou O(P) — heap dá o topo e não sabe
remover do meio nem iterar em ordem; `dict` dá lookup e não dá topo; lista ordenada dá topo
e iteração e não dá inserção barata; `deque` dá as pontas e não dá o meio. E varredura
linear no livro é proibida pela seção 5 do contrato fora dos três casos inerentes.

Há um segundo fato, que é o que torna a escolha uma questão de **composição** e não de
contêiner: as quatro perguntas não vivem na mesma granularidade. Qual é o melhor preço é
pergunta sobre níveis. Onde está esta ordem na fila é pergunta sobre um nível. Que ordem
tem o id 7 não é pergunta sobre preço nenhum — quem cancela não informa preço. Uma
estrutura única teria de responder às três em papéis que não se sobrepõem.

## Decisão

Três camadas, cada uma dona de uma pergunta.

### 1. Fila do nível — lista duplamente encadeada intrusiva

[`domain/order_queue.py`](../../src/matching_engine/domain/order_queue.py). A própria
`Order` é o nó: ela carrega `prev`, `next` e `queue`, e não existe objeto `Node` que a
embrulhe.

Remover deixa de ter busca. O índice global entrega a ordem, a ordem já carrega os
vizinhos, e religar a fila é uma atribuição para cada lado — O(1) real, sem varrer nada. O
encadeamento é **duplo** exatamente por causa desse religamento: desligar um nó exige
reescrever o `next` do anterior, e numa lista simplesmente encadeada achar esse anterior
custaria de volta a varredura que se quer evitar.

O terceiro campo, `queue`, é a alça de pertencimento, e não redundância dos outros dois.
Com ela, pertencer a esta fila é um fato consultável em O(1); sem ela, seria uma inferência
a partir do estado de `prev` e `next` — que é **parcial**, porque uma ordem no miolo de
outra fila tem os dois preenchidos e passaria. A guarda parcial não erraria só na detecção:
a remoção prosseguiria, desligaria a ordem da fila alheia, decrementaria o tamanho desta e
levaria `is_empty` a mentir. Como é `is_empty` que decide se um nível sai do índice de
preços, a corrupção sairia da fila e viraria nível fantasma no livro.

O que se abre mão é do acesso por índice, que o matching não usa: ele consome sempre pela
cabeça, que é a ordem de maior prioridade.

### 2. Índice de níveis por lado — `SortedDict[Ticks, PriceLevel]`

[`domain/book_side.py`](../../src/matching_engine/domain/book_side.py). Um `SortedDict` por
lado, chaveado pelo preço em ticks.

É a única das candidatas que entrega as três coisas de que o lado precisa: lookup por preço
em O(1), melhor preço em O(log P) e iteração ordenada em O(P), sem varredura em nenhuma
delas.

A chave é o preço em ticks **crescente nos dois lados**. Negá-la no lado comprador, para
que a ordenação natural já entregasse o topo, economizaria uma linha e cobraria juros para
sempre: o índice guardaria `-1000` para o preço 10, e todo log, toda depuração e toda
leitura de código exigiriam a conversão mental de volta. O que difere entre os lados é
apenas de qual ponta se lê o topo, e isso é um índice de posição fixado na construção —
`-1` para `BUY`, `0` para `SELL` — passado ao `peekitem`, em vez de um ramo condicional
repetido a cada consulta.

O nível ([`domain/price_level.py`](../../src/matching_engine/domain/price_level.py)) é a
fila mais dois agregados mantidos incrementalmente, `total_quantity` e `non_pegged_count`.
Os dois existem pela mesma assimetria: são consultados muito mais vezes do que mudam, e
recalculá-los sob demanda custaria O(N) no nível — a varredura proibida. Manter custa uma
soma de `int` dentro de operações que já são O(1) e que são as únicas por onde a quantidade
e a composição do nível podem mudar.

### 3. Índice global — `dict[OrderId, Order]`

[`domain/order_book.py`](../../src/matching_engine/domain/order_book.py). Um `dict` de
`order_id` para a ordem viva, atravessando os dois lados.

É por ele que `cancel` e `amend` chegam à ordem sem varrer o livro, e é ele que fecha o
circuito com a camada 1: como a `Order` **é** o nó da fila, o valor devolvido pelo `dict`
já é a alça de remoção em O(1). Não há um segundo mapa de id para nó a manter em sincronia
com o primeiro.

### Como as três se compõem

O cancelamento é o caminho que usa as três e mostra por que elas são o que são:

| Passo | Estrutura | Custo |
|---|---|---|
| Achar a ordem pelo id | índice global | O(1) |
| Achar o nível pelo preço da ordem | `SortedDict` do lado | O(1) |
| Desligar a ordem da fila | ponteiros que ela própria carrega | O(1) |
| Tirar o nível do índice, se esvaziou | `SortedDict` do lado | O(log P) |

Nenhum passo procura. Cada um pergunta a quem sabe.

## Complexidade

Primitivas de cada camada:

| Operação | Custo | Estrutura |
|---|---|---|
| Achar ordem por `order_id` | O(1) | índice global |
| Enfileirar no fim do nível | O(1) | fila intrusiva |
| Remover ordem arbitrária da fila | O(1) | fila intrusiva |
| Executar ou encolher no lugar | O(1) | nível (agregados incrementais) |
| Achar o nível de um preço | O(1) | `SortedDict` |
| Criar nível novo | O(log P) | `SortedDict` |
| Melhor preço do lado | O(log P) | `peekitem` na ponta |
| Melhor preço não-pegged | O(log P) | duas leituras de ponta |
| Tirar nível vazio do índice | O(log P) | `SortedDict` |
| Percorrer os níveis em ordem | O(P) | `SortedDict` |
| Inserir bloco ordenado num nível | O(K + M) | `merge_ordered` |
| Coletar as pegged de um lado | O(K log K) | registro do lado, ordenado por sequência |

Conferência contra as metas da seção 5 do contrato:

| Operação da seção 5 | Meta | Composição | Confere |
|---|---|---|---|
| Inserir limit passiva | O(log P) | achar/criar nível O(log P) + enfileirar O(1) | sim |
| Inserir limit agressiva / market | O(log P + F) | topo O(log P) + F execuções O(1) + repouso do remanescente | sim |
| Best bid / best offer | O(log P) | leitura de ponta | sim |
| Cancel | O(1) esperado, + O(log P) se esvaziar | índice O(1) + fila O(1) + baixa do nível O(log P) | sim |
| Amend (reduz qty) | O(1) | `PriceLevel.reduce` no lugar, sem tocar na fila | sim |
| Amend (preço ou aumenta qty) | O(log P) | remoção O(1) + baixa O(log P) + reinserção O(log P) | sim |
| Reprecificar pegged | O(K log K); + M com intercalação | coleta O(K log K) + merge O(K + M) | sim |
| `print book` | O(N) | uma linha por ordem viva | sim |
| Espaço | O(N + P) | N ordens que são os próprios nós + P níveis | sim |

O melhor preço é O(log P) e fica registrado como tal — ver as alternativas rejeitadas.

## Consequências

- **A `Order` carrega três campos estruturais alheios ao negócio** — `prev`, `next` e
  `queue`. A posse dos três é **exclusiva** da `OrderQueue`, que é o único ponto do sistema
  autorizado a lê-los ou escrevê-los. É o preço da escolha intrusiva, e ele está pago com
  uma alocação por ordem em vez de duas e com a remoção em O(1) sem mapa auxiliar.
- **`total_quantity` e `non_pegged_count` são estado derivado a manter em dia.** O que se
  compra em tempo se paga em coerência. Por isso os dois são invariantes do livro — itens 3
  e 7 da seção 4 —, verificados após cada comando pela suíte de propriedade, e por isso
  `fill` e `reduce` moram no `PriceLevel` em vez de o chamador executar a ordem por fora:
  `order.fill` direto reduziria o remanescente sem tocar no total, e o nível anunciaria
  quantidade que não existe mais.
- **A remoção de nível vazio é responsabilidade explícita do chamador**, por
  `BookSide.remove_if_empty`, e não um efeito automático de `remove`. Não porque seja mais
  simples, mas porque a baixa da ordem não é uma só: quem tira uma ordem do livro tira-a
  também do índice global, que é do `OrderBook` e não do lado. Uma baixa implícita ao lado
  de uma explícita é exatamente como se produz estado fantasma, então o item 2 dos
  invariantes é obrigação visível de quem chama. `OrderBook.remove` é onde as três baixas
  se juntam.
- **Cada camada valida aquilo de que é autoridade**: o lado sobre lateralidade, o nível
  sobre preço, a fila sobre pertencimento. As guardas são de integridade
  (`QueueIntegrityError`, `LevelIntegrityError`, `BookIntegrityError`, todas
  `RuntimeError`), porque quem roteia uma ordem até um nível é a engine — comando
  malformado nunca chega até elas.
- **A fila do nível está sempre ordenada por `sequence_id`**, e não apenas em FIFO de
  chegada. `append` sobre um contador monotônico produz isso naturalmente, o amend que
  renova prioridade recebe um número novo e maior antes de voltar pelo fim, e
  `merge_ordered` preserva a ordem que encontra. É essa propriedade que permite inserir um
  bloco de pegged no meio da fila sem que o desempate entre duas ordens do mesmo preço
  passe a depender de por qual caminho cada uma chegou ao nível.
- **Duas coleções auxiliares acompanham as três camadas**, ambas motivadas pela ADR 0004 e
  não por esta: o registro de pegged de cada `BookSide`, que dá acesso a elas sem varrer a
  fila do nível em que estão, e o registro de *parked* do `OrderBook`, que é o terceiro
  lugar onde uma ordem viva pode estar. As duas devolvem tupla ordenada por `sequence_id`,
  e não a ordem de inserção do `dict`.
- **Remover durante a iteração da fila não é suportado**: zerar os ponteiros da ordem
  corrente interrompe o percurso. O matching não esbarra nisso porque consome pela cabeça,
  nunca iterando.

## Alternativas rejeitadas

**Dois heaps de ordens, um por lado.** É a escolha aparentemente óbvia — prioridade é
exatamente o que um heap ordena, e o topo sai em O(1). Ela quebra nas outras duas
operações. Remoção arbitrária num heap é O(N), porque não há como localizar o elemento sem
procurá-lo; a saída usual é *lazy deletion*, marcar como cancelada e descartar quando
chegar ao topo — o que deixa ordens mortas dentro da estrutura, contaminando qualquer
contagem ou soma que se queira fazer sobre ela e adiando a liberação para um instante que
não se controla. E é **impossível imprimir o livro em ordem** sem desfazer o heap: a
propriedade de heap ordena a raiz, não os irmãos, de modo que sair em ordem exige extrair
tudo e reconstruir. `print book` acontece a pedido do usuário, e cancelamento acontece o
tempo todo. Um heap de **preços**, em vez de ordens, esbarra no mesmo par de problemas um
nível acima: não sabe remover um nível arbitrário — e cancelamento esvazia nível do meio
com frequência, não só do topo — nem iterar em ordem sem se desfazer no caminho.

**`dict` simples de preço para nível.** Lookup em O(1), inserção em O(1), remoção em O(1),
e nada disso resolve: achar o melhor preço vira varredura O(P) sobre as chaves, e o topo é
consultado a cada ordem recebida. Iterar em ordem exigiria ordenar as chaves a cada
impressão, O(P log P). É a estrutura certa para a pergunta errada.

**Lista ordenada de níveis.** O topo está na ponta, a iteração sai de graça e a leitura do
código fica trivial. Cada preço **novo**, porém, desloca a cauda na inserção, O(P) — e um
livro ativo cria e destrói níveis o tempo todo, um por preço que aparece e some. Trocar a
consulta cara pela inserção cara não é progresso quando as duas são frequentes.

**Price ladder — array denso indexado por tick.** É O(1) em tudo: o preço é o índice,
achar o nível é aritmética, o topo se mantém com um cursor, e não há árvore nem hash no
caminho. É o que engines de baixa latência usam de verdade, e **seria a escolha certa aqui
se a faixa de preços fosse fechada e conhecida**. Rejeitada porque ela não é: o enunciado
admite preço decimal arbitrário, e a ADR 0002 o representa como `int` de ticks sem teto. A
memória do ladder é proporcional ao **intervalo** de preços, não ao número de ordens, de
modo que um livro com duas ordens muito distantes pagaria o array inteiro entre elas. Fixar
uma faixa artificial converteria uma decisão de estrutura de dados numa restrição de
negócio que o enunciado não impõe.

**`deque` ou `list` como fila do nível.** As duas são O(1) só nas pontas, e a operação que
decide é a do meio: remover um elemento interno custa a varredura que o encontra, O(N), e a
`list` ainda desloca a cauda depois. Mais grave que o custo é a ausência de **alça estável**
— nenhuma das duas dá uma referência durável a um elemento, porque índices mudam a cada
remoção, e guardar "posição 7" no índice global daria uma alça que aponta para outra ordem
no comando seguinte.

**Nó separado embrulhando a `Order`, em vez de intrusivo.** Mantém a entidade limpa, sem os
três campos estruturais, e é a resposta correta a "não misture negócio com estrutura". Ela
apenas move o problema: o índice global passa a devolver a `Order`, e a remoção precisa do
**nó**, então ou existe um segundo mapa de `order_id` para nó — duas coleções a manter em
sincronia, com uma classe de bug nova em cada operação que insere ou remove —, ou a `Order`
guarda um back-pointer para o nó, que é o mesmo acoplamento de antes com uma indireção a
mais e uma alocação a mais por ordem. O acoplamento não desaparece; só fica mais caro e
menos visível.

**Cache de ponteiro para o melhor nível.** Daria `best_price` em O(1), que é a única meta
da seção 5 que não é constante nem inerente. Rejeitada porque o ponteiro teria de ser
invalidado corretamente em **toda** inserção, remoção e esvaziamento de nível, nos dois
lados — superfície de bug permanente, e do tipo que não falha alto: um cache velho devolve
um preço plausível, e o livro executa contra um topo que já não existe. Em troca, um ganho
que não se mede com P na casa das dezenas. Sub-linear cumpre a exigência do enunciado com
folga, e o custo fica registrado em `BookSide.best_price` em vez de escondido.
