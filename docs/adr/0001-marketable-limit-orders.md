# ADR 0001 — Ordens limit que cruzam o spread são executadas

## Contexto

Uma ordem limit é *marketable* quando seu preço cruza o topo do lado oposto: uma compra a
10 chegando com uma venda a 9 no livro, ou uma venda a 9 chegando com uma compra a 10.
Sem matching, ela deixaria o livro cruzado — `best_bid >= best_ask` —, estado que o item 1
dos invariantes proíbe.

O enunciado permite explicitamente as duas saídas: ignorar a ordem ou preenchê-la, desde
que a escolha seja justificada. Esta ADR justifica.

## Decisão

A ordem limit executa contra o lado oposto enquanto o topo de lá couber dentro do seu
limite de preço, e o remanescente repousa no livro, ao seu próprio preço.

O preço de cada execução é o do **maker** — a ordem passiva. Com um bid de 10 no livro,
uma `limit sell 9` executa a **10**, não a 9: o maker chegou antes, ficou exposto e teve
seu preço acordado primeiro, e o taker aceitou preço igual ou melhor que o seu limite. A
diferença fica com o taker, que é o *price improvement*.

As justificativas, em ordem de força:

1. **Engenharia.** Unifica market e limit em um único algoritmo de matching. Uma market é
   uma limit sem limite de preço, mais a política de descartar o remanescente; com a
   limit agressiva executando, os dois tipos entram no mesmo laço, e o que os distingue
   cabe em dois parâmetros. Rejeitar obrigaria a manter dois caminhos, com risco
   permanente de divergirem no tratamento da execução parcial — o caso raro em teste e
   comum em produção. Reduz pela metade a superfície de teste do matching.
2. **Domínio.** É o comportamento de qualquer exchange real. Uma limit agressiva não é
   anomalia a ser filtrada: é o mecanismo pelo qual o preço se move. Quem quer comprar
   agora manda uma compra acima do bid, ela varre o topo do book, e o preço sobe. Um
   livro que rejeitasse essas ordens teria preço estático por construção.
3. **Corretude.** Rejeitar transformaria em erro uma ordem legítima por causa de uma
   condição de corrida inerente ao mercado: entre o cliente ler o topo do livro e enviar
   a ordem, o topo mudou. O cliente não fez nada de errado, e não há nada que ele pudesse
   ter feito — só há uma janela entre leitura e envio, e ela nunca fecha. Punir o cliente
   por ela é atribuir a ele uma falha que é do desenho.
4. **Expressividade.** Com rejeição, a única forma de comprar acima do bid seria a market
   order, que abre mão de qualquer controle de preço — exatamente o que a limit order
   existe para oferecer. O cliente que quer comprar até 10, mas não a 11, ficaria sem
   nenhuma ordem que expressasse isso.

## Consequências

- A saída fica mais verbosa que alguns exemplos do enunciado sugerem: uma única `limit`
  pode produzir vários `Trade` seguidos de um `OrderAccepted`.
- O item 1 dos invariantes — livro nunca cruzado — passa a ser preservado **por
  construção após o matching**, e não por rejeição na entrada. Ao fim de `_submit`, ou o
  agressor zerou, ou o topo oposto deixou de cruzar o seu limite; nos dois casos o
  remanescente repousa a um preço que não cruza. A garantia é a mesma; o que muda é onde
  ela é produzida.
- Uma limit pode executar a preço melhor que o seu limite, nunca pior. O preço médio de
  execução do taker é, portanto, uma consequência do estado do livro, e não algo que ele
  determine.
- O trade sai ao preço do maker, o que significa que o livro precisa do preço do nível —
  não do preço do agressor — no momento de emitir o evento.

## Alternativas rejeitadas

**Ignorar a ordem que cruzaria.** Cai nos quatro pontos acima. Além disso, obrigaria a
engine a validar contra o topo do livro antes de aceitar qualquer limit, o que reintroduz
no caminho de inserção exatamente a leitura de topo que o matching já faz — com o
inconveniente de que o resultado dessa leitura vira uma recusa em vez de uma execução.

**Executar e cancelar o remanescente**, isto é, tratar toda limit como IOC. Removeria do
cliente a capacidade de deixar ordem passiva a preço agressivo — comprar 300 a 10 quando
só há 100 ofertados a 10, executando o que há e ficando na fila pelo resto —, que é o uso
mais comum de uma limit. Também tornaria impossível *fazer* mercado: nenhuma ordem
repousaria em preço que cruza, então o livro só cresceria com ordens passivas mansas.
IOC é uma política de tempo de vida legítima, mas ela pertence a um tipo de ordem
próprio, não ao comportamento padrão da limit.
