# ADR 0002 — Preço como inteiro de ticks

## Contexto

Comparação e agregação de preço são o núcleo do matching: ordenar o livro, decidir se
duas ordens cruzam, calcular o preço de execução e somar quantidades por nível. Tudo
isso executa a cada ordem recebida.

Ponto flutuante binário não representa 0.01 exatamente. `0.1 + 0.2 != 0.3` é o exemplo
canônico, e o problema não se manifesta como erro: um nível de preço que deveria ser um
vira dois, uma ordem que deveria cruzar não cruza, e o livro é corrompido em silêncio.
Num motor de casamento de ordens isso é inaceitável — o resultado errado é indistinguível
do certo até alguém conferir a conta.

Os preços do enunciado (9.98, 9.99, 10.1, 10.5) têm no máximo duas casas decimais.

## Decisão

Preço é um inteiro de ticks dentro do domínio. `TICKS_PER_UNIT = 100` (tick de 0.01), e
`Ticks = NewType("Ticks", int)` distingue no type checker um preço de um inteiro
qualquer, sem custo em tempo de execução.

Não existe aritmética decimal nem de ponto flutuante em lugar nenhum do sistema —
nem no domínio, nem na fronteira de I/O. Em
[`domain/price.py`](../../src/matching_engine/domain/price.py), a conversão entre texto e
ticks é manipulação de string seguida de aritmética de `int`.

O formato do texto é validado por regex (`^\d+(\.\d+)?$`) **antes** da conversão. Isso
rejeita de uma só vez negativos, notação científica, `NaN`, `Infinity`, string vazia,
espaços em volta, `"10."` e `".5"` — todos valores que `Decimal` aceitaria de bom grado.
O que sobrevive ao regex é dígitos com no máximo um ponto decimal, e daí em diante
separar as duas partes e somar inteiros é suficiente.

## Alternativas rejeitadas

**float** — inaceitável em domínio financeiro pelo motivo acima. O erro é silencioso e
se acumula em agregações; nenhuma tolerância de comparação (`abs(a - b) < eps`) resolve,
porque preços legítimos podem estar a um tick de distância.

**Decimal** — nem no domínio, nem só na fronteira de conversão. Decimal opera sob a
precisão do contexto global (28 dígitos por padrão), então escalar uma entrada com mais
dígitos que isso arredonda em silêncio e faz um sub-tick desaparecer sem erro:
`Decimal("1." + "0" * 29 + "1") * 100` devolve um valor igual a `100`, e o preço fora do
tick passa como válido. É exatamente a falha que este módulo existe para impedir, só que por
outro caminho. Corrigir exigiria gerenciar contexto decimal explicitamente em cada
conversão — precisão calculada a partir do tamanho da entrada, num `localcontext` — o que
é fácil de esquecer na próxima conversão que alguém escrever. `int` resolve por
construção, sem contexto, sem configuração global e sem arredondamento.

Além disso, Decimal é mais lento que inteiro num caminho quente e tem comparação menos
óbvia: `Decimal("10.0") == Decimal("10")` é verdadeiro, mas os dois têm representações
distintas, o que vaza para chaves de dicionário, ordenação estável e serialização. Dado um
tick size fixo, o ganho sobre inteiro é nulo.

## Consequências

- Tick size vira decisão de configuração, com fonte única em `PRICE_DECIMAL_PLACES`
  (`TICKS_PER_UNIT` é derivado dele). Trocá-lo muda a granularidade aceita; mudá-lo com
  estado persistido exigiria migração.
- Toda entrada valida aderência ao tick: preço mais preciso que o tick (`"1.234"`) é
  rejeitado com `InvalidPriceError` em vez de arredondado, para não inventar um preço que
  o cliente não enviou.
- Preço zero é rejeitado.
- A formatação remove zeros à direita (1000 -> `"10"`, 1050 -> `"10.5"`) para reproduzir
  a saída do enunciado.
- Aritmética de preço é exata e ilimitada — o `int` do Python não estoura.
