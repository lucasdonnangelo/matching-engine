# ADR 0004 — Reprecificação de ordens pegged

## Contexto

O requisito 5 do enunciado pede um tipo de ordem sem preço próprio: a *pegged* acompanha o
topo do livro — `peg bid` para o melhor bid, `peg offer` para a melhor offer — e é
reprecificada quando esse topo se move. O exemplo mostra uma pegged que entra atrás dos
`200 @ 10` e que, ao chegar uma `limit buy 10.1 300`, aparece **acima** dela, a `10.1`.

Uma ordem cujo preço é ditado pelo livro abre cinco perguntas que o enunciado não responde,
e nenhuma delas é de implementação:

1. **A que preço, exatamente?** "O topo" é ambíguo assim que existe mais de uma pegged: se
   uma pegged se ancora no topo, e ela própria pode ser o topo, a definição se morde.
2. **A reprecificação custa prioridade?** O requisito 4 é explícito quanto a mudança de
   preço fazer a ordem perder o lugar na fila. Uma pegged muda de preço o tempo todo.
3. **Quando ela acontece?** Uma mudança de topo é um evento contínuo dentro de operações do
   livro; reprecificar no instante errado é reprecificar contra um livro incoerente.
4. **O que acontece quando não há topo?** Um lado sem ordens não oferece referência nenhuma.
5. **Peg cruzado é ordem válida?** `peg offer` numa compra, `peg bid` numa venda.

## Decisão

**A referência é o melhor preço entre as ordens não-pegged do lado.** Não o topo bruto.

**A reprecificação preserva o `sequence_id`.** A ordem mantém a prioridade temporal que
tinha, e um bloco de pegged entra no nível de destino em ordem de sequência, intercalando-se
com quem já está lá — `OrderQueue.merge_ordered`.

**A reconciliação é síncrona e acontece ao fim de cada comando**, para os dois lados, uma
única vez. Não há observers, não há fila de eventos, não há reentrância.

**Sem referência, a ordem fica *parked*:** viva, no índice global, cancelável pelo id, mas
fora dos dois lados e sem preço. Volta ao livro assim que surgir uma não-pegged do seu lado.

**Só o peg homolateral existe:** `bid` com `buy`, `offer` com `sell`. O cruzado é recusado na
construção da `Order`.

## Justificativa

### A referência exclui as pegged, e é isso que quebra a circularidade

Se a referência fosse o topo bruto do lado, cada pegged se ancoraria no preço que outra
pegged acabou de assumir. Com duas pegged num bid cujo melhor preço não-pegged é 10, a
primeira assume 10 e vira o topo; a segunda lê o topo — 10, agora sustentado por uma pegged —
e assume 10; nada se move, e o resultado parece correto. Ele é correto por acidente: a
definição admite estados em que não é. Basta a reprecificação passar a ser feita uma ordem
por vez, ou uma pegged assumir "o topo mais um tick", para que cada passada produza um topo
novo e a seguinte tenha de perseguir esse topo. A terminação passaria a depender do formato
exato do laço, não da definição — e uma definição cuja corretude depende da ordem de
iteração não é uma definição, é uma coincidência mantida por disciplina.

Excluindo as pegged, a referência passa a ser função **apenas** das ordens que têm preço
próprio. Como a reconciliação só move pegged, ela não altera a própria entrada. É o que torna
possível a prova abaixo, e é o que torna `BookSide.best_non_pegged_price` uma consulta de
duas leituras de ponta em vez de uma varredura: todas as pegged de um lado tomam o mesmo
preço, logo se acumulam num único nível, e o item 7 dos invariantes cai como consequência.

### Prova de terminação em uma passada

**Afirmação.** Uma única passada de reconciliação por lado basta, e ela não pode disparar a si
mesma nem produzir execução.

**A referência é invariante sob a reconciliação.** Seja `R` o melhor preço entre os níveis do
lado que contêm alguma ordem não-pegged. A reconciliação move exclusivamente ordens pegged, e
mover uma ordem pegged não altera o conjunto de ordens não-pegged nem o preço de nenhuma
delas. Ela altera o índice de preços apenas de dois modos: cria ou alimenta o nível `R`, que
já existia por definição de `R`; e esvazia o nível de origem, que é retirado do índice se
ficar vazio. Nenhum dos dois cria nível com ordem não-pegged, nem retira nível que contenha
uma. Logo `R` depois da passada é o mesmo `R` de antes. A saída é ponto fixo da entrada, e é
por construção, não por convergência.

**Não há gatilho para uma segunda passada.** O único gatilho de reconciliação é o fim de um
comando, e a reconciliação não é um comando. Como `R` não muda, uma segunda passada
encontraria toda pegged já em `R` e não moveria nada — o que é verificável, e é o que o teste
de "não tocar em quem já está no preço certo" verifica.

**A reprecificação nunca cruza o spread.** O peg é homolateral, então o preço assumido é o
topo do **próprio** lado. Pelo item 1 dos invariantes, `best_bid < best_ask` antes do
comando; uma compra que sobe até o melhor bid não alcança a melhor offer, e uma venda que
desce até a melhor offer não alcança o melhor bid. A ordem reprecificada, portanto, repousa a
um preço que não cruza, e nenhum `Trade` sai da reconciliação. Sem execução, não há mudança
de topo causada por ela; sem mudança de topo, não há cascata.

**Corolário.** A lista de eventos de um comando é finita e determinada antes do fim da
passada: no máximo um `OrderPegged` por ordem pegged viva do lado.

O guard de reentrância em `MatchingEngine._reconcile_pegged` não é defesa contra um caminho
existente — a prova afirma que ele não existe. É um tripwire: se um dia a prova for quebrada
por uma mudança, o primeiro comando falha alto com `RuntimeError` em vez de entrar em laço
ou terminar por sorte.

### O `sequence_id` preservado, e o conflito só aparente com o requisito 4

O requisito 4 diz que alterar o preço de uma ordem faz com que ela perca a prioridade. Uma
pegged muda de preço a cada movimento do topo. Lido ao pé da letra, o requisito 4 mandaria
mandá-la ao fim da fila a cada tick.

O conflito é aparente porque as duas regras falam de coisas diferentes. **A perda de
prioridade do requisito 4 pune a alteração iniciada pelo cliente**, e a razão é a da ADR
0005: a fila é recurso escasso, e quem muda de ideia está pedindo mais fila do que a que lhe
foi concedida, às custas de quem se enfileirou atrás confiando no que o livro anunciava. A
reprecificação de uma pegged não parte do cliente. Ela parte da engine, cumprindo
literalmente o contrato que a ordem pediu ao ser enviada: *acompanhe o topo*. O cliente não
mudou de ideia — ele expressou esta ideia desde o começo, e é a engine que a executa.

Punir esse movimento seria cobrar prioridade por algo que o dono da ordem não fez e não pode
evitar, e cobrá-la repetidamente, a cada oscilação do mercado. O tipo de ordem ficaria
inútil: uma pegged desceria ao fim da fila toda vez que o topo se mexesse, isto é, seria
sistematicamente a última a executar exatamente no preço que ela sempre acompanha. Ninguém
enviaria uma.

E é o que faz o exemplo do enunciado fechar. Lá, a pegged reprecificada aparece **acima** da
`limit buy 10.1 300` que provocou a mudança. Ela só pode aparecer acima se conservou a
prioridade de quando chegou — e é justo que conserve, porque ela já estava no livro quando a
limit chegou. Reprecificar não é reenviar.

A contrapartida está na entrada: a pegged que **chega** entra pelo **fim** da fila do nível
de referência, atrás dos `200 @ 10` do exemplo, porque nesse instante ela é de fato a ordem
mais nova do livro. As duas regras são a mesma regra — a fila é ordenada por `sequence_id`, e
o `sequence_id` diz quando a ordem chegou — aplicada a dois instantes diferentes.

### Síncrona ao fim do comando, e não Observer nem fila de eventos

**Observer** é a solução de manual: o livro notifica listeners quando o topo muda, e a
reprecificação é um listener. Ela dispara no pior instante possível. A mudança de topo
acontece *dentro* de `OrderBook.remove`, entre a saída da ordem do nível e a saída do nível
vazio do índice de preços — o listener veria um livro em estado transitório, com nível vazio
ainda indexado ou com o índice global já baixado, e reprecificaria contra ele. Pior: o
listener mexe no livro, e mexer no livro dispara o listener. A reentrância deixaria de ser
hipótese e viraria caminho comum, e a prova acima deixaria de valer não por ser falsa, mas
por deixar de haver um "fim" bem definido em que ela se aplica. É a pior classe de bug que se
pode plantar aqui: estado meio consistente, observado por código que o assume consistente.

**Fila de eventos** — enfileirar as reprecificações e drená-las depois — resolve a
reentrância e introduz assincronia que ninguém pediu. Passaria a existir um instante em que o
comando terminou e o livro ainda não está reconciliado, e a pergunta "o `print book` que
vem a seguir mostra o livro antes ou depois da drenagem?" não teria resposta determinística
sem, na prática, drenar a fila ao fim do comando — que é a decisão tomada, com uma fila a
mais no caminho.

**Ao fim do comando**, cada comando é **atômico** do ponto de vista do cliente: ele nunca
observa um livro meio reconciliado, porque entre o início e o fim de um comando não há nada a
observar. O custo é uma consulta de topo por lado por comando, O(log P), e ela é O(1) quando
não há pegged nenhuma — as duas listas vêm vazias e a função devolve sem tocar no livro.

A reconciliação corre nos **dois** lados a cada comando, e não só no lado do comando, porque
um comando atinge os dois: uma compra agressiva consome ofertas e pode desfazer a referência
das pegged do lado **vendedor**. Escolher o lado a reconciliar exigiria, em cada comando, um
raciocínio sobre qual lado mudou — raciocínio que erra em silêncio, e cujo erro só aparece
como pegged parada no preço errado muitos comandos depois.

### *Parked* em vez de cancelar

Quando a última não-pegged de um lado sai — cancelada ou executada —, as pegged daquele lado
perdem aquilo a que se ancoravam. Cancelá-las seria descartar a intenção do cliente por causa
de um estado que pode durar um único comando: basta chegar uma limit para que a referência
exista de novo. Inventar um preço para elas seria mentir sobre o livro, e um preço inventado
é, por definição, um preço que ninguém pediu. Deixá-las no livro sem referência é a pior das
três, porque elas passariam a sustentar sozinhas um nível de preço — o livro anunciaria
profundidade cujo preço não tem quem o sustente, e uma agressiva executaria contra ele.

*Parked* preserva as três coisas ao mesmo tempo: a intenção (a ordem existe), a verdade do
livro (ela não aparece em nenhum lado, porque não tem preço) e o controle do cliente (ela
está no índice global, e `cancel <id>` funciona). O que se perde enquanto ela espera é a
exposição — que é exatamente o que deixou de existir.

### Só peg homolateral

`peg offer` numa compra assumiria o preço do **lado oposto**: a compra passaria a valer a
melhor oferta de venda. Esse preço cruza o spread por definição, então a ordem seria
*marketable* no instante em que nascesse. Não é um tipo de ordem exótico a suportar — é uma
market disfarçada, e com um agravante: o remanescente repousaria e seria reprecificado para o
novo topo oposto a cada movimento, executando de novo, e de novo. Um laço de reprecificação e
execução que o cliente não pediu e que a prova de terminação não cobre, porque é justamente a
hipótese da homolateralidade que ela usa.

O custo de barrá-lo é uma comparação na construção da `Order`, e é lá que ela mora — não no
parser, não na engine —, porque é uma regra sobre o que uma ordem **é**. Assim `peg bid sell`
não existe como objeto, e nenhuma camada adiante precisa reverificá-lo.

## Consequências

- `MatchingEngine._reconcile_pegged` é chamada ao fim de `submit_limit`, `submit_market`,
  `submit_pegged`, `cancel` e `amend`, para os dois lados. Um comando pode, portanto, emitir
  `OrderPegged` sem que o cliente tenha mencionado ordem pegged nenhuma — é o efeito visível
  de a engine cumprir o contrato de outra ordem.
- Um só evento, `OrderPegged`, relata a entrada e cada reprecificação, com `price` opcional:
  `None` **é** o estado *parked*. `io/presenter.py` imprime `Order pegged: buy 150 @ 10.1 2`
  ou `Order pegged: buy 150 parked 2`. O enunciado não especifica essa saída; a escolha
  espelha a linha de ordem criada, e está registrada em `presenter._pegged_line`.
- O item 7 dos invariantes — no máximo um nível pegged-only, e ele é o topo — descreve a
  **janela transitória** entre a saída da última não-pegged de um nível e a reconciliação que
  tira as pegged de lá. Ao fim de cada comando a janela já fechou, e o número de níveis
  pegged-only é zero. `best_non_pegged_price` existe para ler corretamente essa janela, e por
  isso ela precisa estar nomeada nos invariantes.
- O livro ganha uma operação de inserção de bloco que atravessa as camadas —
  `OrderQueue.merge_ordered`, `PriceLevel.merge_ordered`, `BookSide.merge_ordered`,
  `OrderBook.merge_ordered` —, porque preservar o `sequence_id` significa inserir no **meio**
  da fila, e `add` só sabe inserir no fim.
- `BookSide` mantém um registro das pegged que estão no livro. Sem ele, alcançá-las custaria
  O(M) na fila do nível em que estão, e a meta O(K) da seção 5 do contrato cairia justamente
  no caso comum — o da limit nova melhorando o topo. O registro é mantido em `add` e
  `remove`, que são as duas únicas portas de um lado, e por isso não tem como divergir.
- `Order.repeg_to` é a única porta por onde o preço de uma pegged muda, e ela exige a ordem
  fora de qualquer fila — a mesma guarda de `amend_to`, pelo mesmo motivo: o nível é a
  autoridade sobre o preço do que guarda.
- A reprecificação é o **único** custo linear não inerente do sistema, e é cobrado com
  parcimônia: O(K) no caso comum, O(K + M) só quando o topo muda por cancelamento e há
  intercalação real. Ver `OrderQueue.merge_ordered` e a seção 5 do contrato.
- Ordem pegged *parked* continua não sendo alterável por `modify`: sem preço e fora dos dois
  lados, nenhuma das duas políticas da ADR 0005 se aplica a ela. Cancelá-la funciona.

## Alternativas rejeitadas

**Ancorar no topo bruto do lado, pegged incluídas.** É a leitura literal de "acompanha o
topo" e dispensa `non_pegged_count`. Rejeitada pela circularidade descrita acima: a
terminação passaria a depender do formato do laço de reprecificação, e não da definição de
referência. Também custaria a consulta de duas leituras de ponta — sem a garantia de que as
pegged se acumulam num único nível, achar o preço de referência voltaria a ser varredura.

**Mandar as pegged reprecificadas para o fim da fila do nível de destino.** É O(K) sempre,
dispensa `merge_ordered` nas quatro camadas e elimina o único custo linear não inerente do
sistema. Rejeitada por contradizer o exemplo do enunciado, no qual a pegged reprecificada
aparece **acima** da limit que provocou a mudança — e por contradizer o argumento que
sustenta o exemplo: a pegged já estava no livro, e reprecificar não é reenviar. O linear aqui
é o preço de uma decisão de negócio, e não uma limitação de estrutura de dados.

**Renovar a prioridade a cada reprecificação**, aplicando o requisito 4 ao pé da letra.
Rejeitada porque esvazia o tipo de ordem: a pegged seria sistematicamente a última da fila no
único preço que ela acompanha. Além disso, contradiz o exemplo do enunciado de forma
verificável — a ordem das linhas do `print book` inverteria.

**Observer sobre a mudança de topo do livro.** Rejeitada acima: dispara com o livro em estado
transitório e transforma a reentrância em caminho comum.

**Fila de eventos drenada depois.** Rejeitada acima: introduz assincronia não pedida e, para
recuperar o determinismo, acaba tendo de drenar ao fim do comando — que é a decisão tomada,
com uma indireção a mais.

**Cancelar as pegged que ficam sem referência.** Descarta a intenção do cliente por causa de
um estado que pode durar um comando. Também tornaria o tipo de ordem inseguro num livro fino,
que é justamente onde a referência some com mais frequência.

**Deixá-las no livro, sem referência, ao último preço conhecido.** É a alternativa que mais
parece inofensiva e é a pior: a ordem passaria a sustentar sozinha um nível de preço que
nenhuma ordem com preço próprio sustenta, o livro anunciaria profundidade a um preço que
ninguém pediu, e uma agressiva executaria contra ela. Uma pegged sem referência não é uma
ordem a 10 — é uma ordem sem preço, e o livro é indexado por preço.

**Aceitar peg cruzado como um tipo de ordem a mais.** Rejeitada acima: é uma market
disfarçada com laço de reprecificação, e é a hipótese cuja negação sustenta a prova de
terminação.
