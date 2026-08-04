# Contexto de engenharia — Matching Engine

Contrato do projeto. Vale para qualquer pessoa ou ferramenta que escreva código aqui.
Decisões arquiteturais registradas neste documento não são reabertas sem discussão
explícita.

---

## 1. O que o sistema é

Matching engine de ativo único, em memória, com prioridade **preço-tempo**.

- Tipos de ordem: `limit`, `market`, `pegged` (peg to bid / peg to offer).
- Operações: inserir, cancelar, alterar, visualizar o livro.
- Interface: REPL de linha de comando.
- Fora de escopo, por decisão: persistência, concorrência, múltiplos ativos,
  self-trade prevention, escalabilidade horizontal.

---

## 2. Arquitetura em camadas — regra inegociável

```
src/matching_engine/
├── domain/          # regras de negócio puras
│   ├── side.py          Side, PegReference
│   ├── price.py         Ticks, parse_price, format_price
│   ├── order.py         Order (dado + ponteiros de fila + remaining)
│   ├── events.py        Trade, OrderAccepted, OrderCancelled, OrderAmended,
│   │                    OrderRejected, BookSnapshot
│   ├── order_queue.py   lista duplamente encadeada intrusiva (append, remove e
│   │                    alça de pertencimento em O(1))
│   ├── price_level.py   fila FIFO + total_quantity + non_pegged_count
│   ├── book_side.py     SortedDict[ticks -> PriceLevel], best, registro de pegged
│   ├── order_book.py    dois BookSide + índice global de ordens
│   └── engine.py        matching, cancel, amend, reconciliação de pegged
└── io/              # fronteira com o mundo
    ├── commands.py      DTOs de comando
    ├── parser.py        texto -> Command
    ├── presenter.py     Event -> texto
    └── cli.py           REPL
```

**Restrições que não podem ser violadas:**

- `domain/` **nunca** importa de `io/`.
- `domain/` **nunca** usa `print`, nunca faz parsing de comando, nunca formata saída.
- A engine retorna `list[Event]`. Converter evento em texto é responsabilidade
  exclusiva de `io/presenter.py`.
- Conversão de texto para tipo do domínio (`"buy"` → `Side.BUY`) acontece em `io/`,
  não em `domain/`.

Violação dessas regras é motivo de rejeição em revisão.

---

## 3. Decisões tomadas

| Tema | Decisão | Racional |
|---|---|---|
| Representação de preço | Inteiro de ticks (`Ticks`), tick de 0.01 | `float` é inexato em binário; `Decimal` arredonda sub-ticks em silêncio sob precisão de contexto. Ver ADR 0002 |
| Marketable limit order | Executa contra o lado oposto; remanescente repousa no livro | Unifica market e limit em **um único** algoritmo de matching; é o comportamento de exchange real. Ver ADR 0001 |
| Preço do trade | Preço do **maker** (ordem passiva) | Convenção universal; o taker recebe price improvement quando aplicável |
| Market order | Limit com preço ilimitado + IOC; remanescente é descartado | Inferido dos exemplos do enunciado |
| Amend — reduzir qty | Mantém prioridade, alteração in place | Não prejudica ninguém na fila |
| Amend — mudar preço ou aumentar qty | Perde prioridade (cancel + resubmit) | Fila é recurso escasso; aumentar qty sem pagar tempo é injusto |
| Amend — identidade | Mantém `order_id`, recebe novo `sequence_id` | Separar identidade (cliente) de prioridade (fila) |
| Peg — lateralidade | Apenas homolateral: `bid`↔`buy`, `offer`↔`sell`. Cruzado é rejeitado | Peg homolateral nunca cruza o spread ⇒ reprecificação nunca dispara matching |
| Peg — referência | Melhor preço entre ordens **não-pegged** do lado | Evita dependência circular entre pegged; garante terminação em uma passada |
| Peg — sem referência | Ordem fica *parked* fora do livro, reativada quando surgir referência | Preserva a intenção do cliente |
| Peg — reprecificação | Mantém o `sequence_id` original | Bate com o exemplo do enunciado; a perda de prioridade do requisito 4 governa alterações iniciadas pelo **cliente**, não pela engine |
| Peg — gatilho | Reconciliação síncrona ao fim de cada comando | Observer traz reentrância; fila de eventos traz assincronia não pedida. Cada comando é atômico para o cliente |
| Saída de trades | Domínio emite um `Trade` por par maker/taker; o presenter agrega por preço | O exemplo do enunciado agrega, mas o domínio precisa de granularidade auditável |
| Order: identidade | `eq=False`; igualdade é por identidade, não por estrutura | `Order` é entidade mutável, identificada pelo `order_id`; duas ordens de campos idênticos são ordens diferentes |
| Order: remaining | Não é parâmetro do construtor; nasce igual a `quantity` e só encolhe por `fill` | Nenhum caminho legítimo cria ordem já parcialmente executada; o estado inválido fica inconstruível em vez de validado |
| Fila do nível | Lista duplamente encadeada intrusiva; a `Order` é o nó e carrega a alça para a fila dona | A operação que decide é a remoção arbitrária em O(1); a alça torna as guardas de integridade totais por comparação de identidade |
| Idioma | Código e identificadores em inglês; mensagens de erro, livro, docstrings, ADRs e README em português. As strings literais fixadas pelo enunciado são reproduzidas exatamente como especificadas | O enunciado é bilíngue: exige `Trade, price:` e `Order cancelled` em inglês, mas o cabeçalho do livro em português |

---

## 4. Invariantes do livro

Toda operação preserva, e a suíte de propriedade verifica após **cada** comando:

1. `best_bid < best_ask` — o livro nunca fica cruzado.
2. Nenhum `PriceLevel` vazio permanece no índice de preços.
3. `level.total_quantity` == soma de `remaining` das ordens do nível.
4. Toda ordem viva está no índice global **e** em exatamente um nível, ou está *parked*.
5. Conservação: Σ quantidade executada do agressor == Σ quantidade consumida dos passivos.
6. Toda ordem pegged está no melhor preço não-pegged do seu lado, ou *parked*.
7. No máximo um nível pode conter apenas ordens pegged, e ele é o topo do lado.

---

## 5. Metas de complexidade

`N` = ordens vivas · `P` = níveis de preço ativos · `F` = fills · `K` = pegged do lado ·
`M` = ordens no nível de destino.

| Operação | Alvo |
|---|---|
| Inserir limit passiva | O(log P) |
| Inserir limit agressiva / market | O(log P + F), amortizado O(1) por fill |
| Best bid / best offer | O(1) |
| Cancel | O(1) esperado, + O(log P) se o nível esvaziar |
| Amend (reduz qty) | O(1) |
| Amend (preço ou aumenta qty) | O(log P) |
| Reprecificar pegged | O(1) no caso comum (splice); O(K + M) no pior caso (merge) |
| `print book` | O(N) — inerente à saída |
| Espaço | O(N + P) |

**Varredura linear no livro é proibida**, salvo os três casos inerentes:
impressão do livro, matching (linear no número de fills) e o merge da reprecificação
de pegged.

---

## 6. Convenções de código

- Python 3.12+ (o piso do `requires-python` é o contrato; o runtime local é 3.13).
- `mypy` em modo strict e `ruff` devem passar sempre.
- **Nenhum `float`. Nenhum `Decimal`.** Preço é `Ticks` (int). Quantidade é `int`.
- Prioridade temporal é um `sequence_id` monotônico global — **nunca** relógio de
  parede (não monotônico, colide, e torna testes não determinísticos).
- Sem abstração especulativa: nada de interface com uma implementação só, nada de
  padrão de projeto sem necessidade demonstrada, nada de camada de indireção "para o
  futuro".
- Se uma implementação exige explicação sobre um mecanismo obscuro da linguagem,
  prefira a versão mais simples e explícita, mesmo que mais longa.
- Código e identificadores em inglês; docstrings, comentários, ADRs e README em
  português.
- Docstrings explicam **por que**, não **o que**.
- Exceção de domínio derivada de `ValueError` (`InvalidPriceError`, `InvalidOrderError`)
  sinaliza entrada inválida do usuário; derivada de `RuntimeError`
  (`QueueIntegrityError`, `LevelIntegrityError`) sinaliza inconsistência interna da
  engine — comando malformado nunca chega a elas.

---

## 7. Testes

- **Unitários** (`tests/unit/`): viajam no mesmo commit que o código que testam.
- **Propriedade** (`tests/property/`, Hypothesis): sequências aleatórias de comandos
  verificando as sete invariantes da seção 4 após cada comando.
- **End-to-end golden** (`tests/e2e/`): os exemplos do enunciado reproduzidos byte a
  byte. Line endings são LF (ver `.gitattributes`).

Antes de considerar uma etapa pronta: casos felizes, entradas inválidas, execução
parcial, execução total, livro vazio, múltiplos níveis de preço, FIFO dentro do nível,
cancelamento, alteração e ordens pegged.

---

## 8. Regras de trabalho para agentes

- **Não criar commits. Não rodar `git`.** O histórico é curado manualmente e faz parte
  da avaliação do projeto.
- Não criar arquivos além dos explicitamente pedidos.
- Não adicionar dependências. As permitidas são: `sortedcontainers`, `pytest`,
  `hypothesis`, `ruff`, `mypy`.
- Antes de encerrar uma tarefa, `ruff check .`, `mypy` e `pytest` devem passar.
- **Se uma instrução conflitar com este documento, com um teste existente ou consigo
  mesma, pare e aponte o conflito** em vez de escolher sozinho qual lado seguir.
