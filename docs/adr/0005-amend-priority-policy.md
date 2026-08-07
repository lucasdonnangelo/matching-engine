# ADR 0005 — Política de prioridade na alteração de ordem

## Contexto

O requisito 4 do enunciado exige alterar preço e quantidade de uma ordem já enviada, e diz
uma coisa só sobre prioridade: **mudança de preço faz a ordem perder o lugar na fila**. O
exemplo confirma — com `200 @ 10` e `100 @ 9.99` na compra, alterar a de 200 para 9.98
produz um livro em que os 100 @ 9.99 estão acima dos 200 @ 9.98.

O que acontece com a **quantidade** fica em aberto. E o silêncio não é neutro: prioridade é
o único recurso que o livro distribui, e cada amend ou preserva ou redistribui esse recurso
entre clientes que não têm como se defender do critério escolhido.

Há ainda uma segunda pergunta que o enunciado não formula, e que só aparece quando a ordem
foi parcialmente executada: `qty 80` compara com o quê? Com a quantidade que o cliente
enviou, ou com o saldo que ainda resta por executar?

## Decisão

**Reduzir quantidade mantém a prioridade. Aumentar quantidade renova. Mudar preço renova.**
Preço e quantidade alterados no mesmo comando renovam, porque basta um dos dois motivos.

Renovar é, literalmente, `cancel + resubmit` preservando a identidade: a ordem sai do livro,
recebe um `sequence_id` novo — o `order_id` **não** muda, porque é por ele que o cliente
segue chamando `cancel` e `modify` —, passa pelo mesmo caminho de matching de uma ordem
nova e volta ao fim da fila do nível de destino. Manter é alteração in place, sem tocar na
fila: a ordem continua exatamente onde estava.

**A comparação é contra `quantity`, e não contra o remanescente.** Uma ordem de 100 com 40
já executados tem 60 de saldo. O pedido `qty 80` é:

| Base de comparação | Leitura | Política |
|---|---|---|
| `quantity` = 100 | 100 → 80 | **redução**, mantém prioridade |
| `remaining` = 60 | 60 → 80 | **aumento**, renovaria prioridade |

O mesmo número, duas políticas opostas. A base é a `quantity` porque é ela que o cliente
enviou e é dela que ele se lembra: quem pediu 100 e agora pede 80 está encolhendo o pedido,
independentemente de quanto já foi atendido. Lida contra o saldo, a política passaria a
depender de quanto a ordem executou desde o envio — quantidade que o cliente não controla e
pode nem conhecer no instante em que digita —, e o mesmo comando custaria prioridade ou não
conforme o mercado tivesse ou não atingido a ordem no meio do caminho.

O novo saldo é, portanto, o que resta do pedido novo: `nova_quantidade - já_executado`. Daí
decorre a recusa de reduzir para um valor menor ou igual ao já executado: os 40 negociados
não voltam, e uma ordem de 100 com 40 executados não pode virar uma ordem de 30. É pedido
impossível, não ajuste a arredondar, e vira erro de usuário — a sessão continua.

## Justificativa

A fila é recurso escasso, e a única moeda com que se paga por ela é o tempo.

**Aumentar sem pagar tempo pegaria mais fila às custas de quem entrou depois.** Uma ordem de
1 unidade no topo da fila poderia virar uma ordem de 10.000 sem sair do lugar, passando à
frente de todas as ordens que se posicionaram atrás dela enquanto ela era pequena. Quem se
enfileirou o fez diante de um livro que anunciava 1 unidade adiante; a estratégia óbvia
seria enfileirar-se cedo com o mínimo e crescer na hora da execução. A prioridade deixaria
de significar "chegou antes com este tamanho" e passaria a significar "chegou antes",
esvaziando a informação que o livro publica.

**Reduzir não prejudica ninguém.** Quem está atrás na fila só melhora: há menos quantidade
adiante do que havia. Cobrar prioridade por isso puniria o cliente por liberar liquidez que
ele havia reservado, e o incentivo seria perverso — sabendo que reduzir custa o lugar, o
cliente racional prefere manter a ordem grande e cancelar tarde, o que deixa o livro
anunciando profundidade que o dono já não quer honrar.

**É a convenção de mercado.** Bolsas e ECNs tratam redução de quantidade como alteração in
place e aumento como nova ordem, pelas mesmas duas razões. Um livro que se comportasse de
outro jeito não seria só diferente: seria explorável de um modo que quem opera reconhece.

## Consequências

- `amend` tem dois caminhos com custos distintos: O(1) na redução pura — o índice global
  acha a ordem, o índice de preços acha o nível, o resto é uma subtração — e O(log P + F)
  quando renova, que é o custo de uma submissão, já que é isso que a renovação faz.
- Um amend que renova passa pelo **mesmo** matching de uma ordem nova, o que significa que
  um amend pode executar. Ver ADR 0001: preço agressivo é preço agressivo, tenha vindo de um
  submit ou de um amend. Se a alteração executa a ordem por inteiro, saem só os `Trade` e
  nenhum `OrderAmended` — não sobrou nada para repousar.
- Amend e `cancel + resubmit` deixam de ser equivalentes, e é essa diferença que dá função
  ao comando: um preserva prioridade e identidade, o outro não preserva nem uma nem outra.
- `OrderAmended` carrega `priority_renewed`, que não aparece na saída. Sem ele, uma ordem de
  300 reduzida para 200 e uma de 100 aumentada para 200 produziriam eventos idênticos,
  embora só a segunda tenha ido para o fim da fila.
- Reduzir altera a `quantity` da ordem, não só o saldo: depois de reduzir, a ordem **é** de
  outro tamanho. Sem isso, encolher de 100 para 80 e voltar para 90 seria lido como redução,
  e o cliente teria achado o caminho para crescer de graça — encolher e voltar.
- A ordem alterada mantém o `order_id` e recebe `sequence_id` novo, o que exige contadores
  separados para os dois no `OrderBook`.
- Para ordens pegged, a política vale apenas para a quantidade: alteração de **preço** sobre
  pegged é recusada sempre, porque o preço de uma pegged é delegado à engine — ver ADR 0004.
  Alteração de **quantidade** vale quando a ordem está no livro; a *parked*, fora dos dois
  lados, não é alterável, apenas cancelável.

## Alternativas rejeitadas

**Qualquer amend renova a prioridade.** É a leitura mais simples, a mais fácil de
implementar — um caminho só — e é literalmente compatível com o enunciado, que exige
renovação para preço e nada diz sobre quantidade. Rejeitada porque pune uma operação que não
prejudica terceiro nenhum, e o preço disso é duplo: o cliente é levado a manter no livro
quantidade que já não quer, e o comando `modify` perde a razão de existir — se toda alteração
custa o lugar na fila, ele é indistinguível de `cancel` seguido de nova ordem, com a única
diferença de preservar o `order_id`. Uma operação cujo efeito é o de duas outras juntas não
paga o seu custo em superfície de teste.

**Comparar a quantidade nova contra o remanescente.** Tem um argumento a favor: é o número
que o cliente vê no `print book`. Rejeitada porque torna a política dependente da execução —
o mesmo comando, sobre a mesma ordem, custa prioridade ou não conforme o mercado a tenha
atingido no intervalo entre a leitura e a digitação. Isso é uma condição de corrida que o
cliente não pode fechar, do mesmo tipo que a ADR 0001 se recusa a transformar em erro.

**Manter a prioridade também no aumento, cobrando só a quantidade acrescida** — dividir a
ordem em duas, a parte antiga no lugar e a parte nova no fim da fila. É defensável, e existe
em alguns mercados. Rejeitada por custo: uma ordem passaria a ocupar duas posições na fila e
o `order_id` deixaria de identificar uma posição, o que atinge a remoção em O(1), o índice
global e a impressão do livro — muita estrutura para um comportamento que o enunciado não
pede. Nada no desenho impede adotá-la depois; a decisão é de escopo, não de princípio.
