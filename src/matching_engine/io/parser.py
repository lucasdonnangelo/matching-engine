"""Texto -> ``Command``. É aqui que a linha digitada deixa de ser texto.

Nenhuma validação de negócio acontece neste módulo. O parser reconhece a forma da linha e
converte cada token no tipo do domínio correspondente — ``"buy" -> Side.BUY``,
``"10.5" -> Ticks(1050)`` —, e é só. Se a ordem resultante é aceitável, se o peg cruzado é
proibido, se a quantidade cabe no livro: tudo isso é decisão do domínio, tomada uma vez, no
lugar onde a regra mora. Duplicar a verificação aqui pareceria defesa em profundidade e
seria, na prática, duas fontes para a mesma regra — que divergem na primeira mudança e
passam a discordar sobre o que é válido.

O que o parser recusa é o que só ele pode recusar: token que não é número onde se espera
número, palavra-chave desconhecida, argumento faltando ou sobrando, chave repetida. Tudo
sai como ``ParseError``, de modo que o chamador tem **um** tipo de exceção para tratar em
vez de um conjunto aberto vindo de cada conversor usado no caminho.

As palavras-chave são reconhecidas sem diferenciar maiúsculas de minúsculas, mas as
mensagens de erro citam o token exatamente como foi digitado: quem errou precisa se
reconhecer na mensagem.
"""

from __future__ import annotations

import re
from typing import Final

from matching_engine.domain.order import OrderId
from matching_engine.domain.price import InvalidPriceError, Ticks, parse_price
from matching_engine.domain.side import PegReference, Side
from matching_engine.io.commands import (
    CancelOrder,
    Command,
    ModifyOrder,
    PrintBook,
    Quit,
    SubmitLimit,
    SubmitMarket,
    SubmitPegged,
)

_USAGE: Final = {
    "limit": "limit <buy|sell> <preço> <quantidade>",
    "market": "market <buy|sell> <quantidade>",
    "peg": "peg <bid|offer> <buy|sell> <quantidade>",
    "cancel": "cancel order <id>",
    "modify": "modify order <id> [price <preço>] [qty <quantidade>]",
    "print": "print book",
}

_DIGITS: Final = re.compile(r"\d+")


class ParseError(ValueError):
    """Linha que não corresponde a comando nenhum da gramática.

    ``ValueError`` pelo critério da seção 6 do contrato: descreve entrada inválida do
    usuário, que o ``Cli`` converte em uma linha de erro e a sessão continua. É a mesma
    família de ``InvalidPriceError`` e ``InvalidOrderError``, e por isso a fronteira trata
    as três de uma vez, sem enumerar exceção por exceção.
    """


def parse(line: str) -> Command | None:
    """Converte uma linha em comando; ``None`` quando não há comando nenhum na linha.

    Linha vazia ou só com espaços devolve ``None`` em vez de erro: apertar Enter num REPL
    não é engano a corrigir, é uma linha em branco. Devolver ``None`` distingue esse caso
    de um comando válido sem produzir exceção para fluxo normal — o REPL simplesmente
    volta a pedir entrada.

    ``split`` sem argumento colapsa qualquer sequência de espaços e tabulações e ignora as
    das pontas, então espaçamento irregular não é erro de sintaxe.
    """
    tokens = line.split()
    if not tokens:
        return None

    keyword, arguments = tokens[0].lower(), tokens[1:]
    match keyword:
        case "limit":
            _expect_arity(keyword, arguments, 3)
            return SubmitLimit(
                side=_parse_side(arguments[0]),
                price=_parse_price(arguments[1]),
                quantity=_parse_quantity(arguments[2]),
            )
        case "market":
            _expect_arity(keyword, arguments, 2)
            return SubmitMarket(
                side=_parse_side(arguments[0]),
                quantity=_parse_quantity(arguments[1]),
            )
        case "peg":
            _expect_arity(keyword, arguments, 3)
            return SubmitPegged(
                reference=_parse_peg_reference(arguments[0]),
                side=_parse_side(arguments[1]),
                quantity=_parse_quantity(arguments[2]),
            )
        case "cancel":
            _expect_arity(keyword, arguments, 2)
            _expect_literal(keyword, arguments[0], "order")
            return CancelOrder(order_id=_parse_order_id(arguments[1]))
        case "modify":
            return _parse_modify(arguments)
        case "print":
            _expect_arity(keyword, arguments, 1)
            _expect_literal(keyword, arguments[0], "book")
            return PrintBook()
        case "quit" | "exit":
            if arguments:
                raise ParseError(
                    f"argumento a mais em {keyword}: {arguments[0]!r}; "
                    f"forma esperada: {keyword}, sem argumentos"
                )
            return Quit()
        case _:
            raise ParseError(f"comando desconhecido: {tokens[0]!r}")


def _parse_modify(arguments: list[str]) -> ModifyOrder:
    """``modify order <id>`` seguido de pares chave/valor, em qualquer ordem.

    Os pares são posicionais entre si mas nomeados, e é a nomeação que torna possível
    alterar só a quantidade sem repetir o preço. O preço da flexibilidade são três recusas
    que uma sintaxe fixa não precisaria: chave repetida, chave desconhecida e valor
    faltando. Nenhuma delas é preciosismo — ``modify order 1 price 10 price 11`` é uma
    contradição do usuário, e escolher em silêncio um dos dois preços altera a ordem de um
    jeito que ele não pediu.

    Nenhum par também é recusado: uma alteração que não altera nada é engano de digitação,
    não pedido a executar.
    """
    usage = _USAGE["modify"]
    if len(arguments) < 2:
        raise ParseError(f"forma esperada: {usage}")
    _expect_literal("modify", arguments[0], "order")
    order_id = _parse_order_id(arguments[1])

    pairs = arguments[2:]
    if not pairs:
        raise ParseError(f"modify exige ao menos price ou qty; forma esperada: {usage}")

    price: Ticks | None = None
    quantity: int | None = None
    while pairs:
        key, rest = pairs[0], pairs[1:]
        if not rest:
            raise ParseError(f"falta o valor de {key!r}; forma esperada: {usage}")

        match key.lower():
            case "price":
                if price is not None:
                    raise ParseError(f"chave repetida em modify: {key!r}")
                price = _parse_price(rest[0])
            case "qty":
                if quantity is not None:
                    raise ParseError(f"chave repetida em modify: {key!r}")
                quantity = _parse_quantity(rest[0])
            case _:
                raise ParseError(f"chave desconhecida em modify: {key!r}; esperado price ou qty")

        pairs = rest[1:]

    return ModifyOrder(order_id=order_id, price=price, quantity=quantity)


def _expect_arity(keyword: str, arguments: list[str], expected: int) -> None:
    """Recusa argumento a menos e a mais pela mesma porta, com mensagens distintas.

    Argumento sobrando é recusado, e não ignorado, porque ``limit buy 10 100 200`` quase
    certamente não é a ordem que o usuário quis mandar. Aceitar em silêncio executaria
    uma ordem de verdade a partir de uma linha que ele não conferiu.

    As duas mensagens dizem a forma esperada **e** o que houve de concreto, porque só a
    forma esperada obriga o usuário a conferir a linha token a token para achar a
    diferença. Sobrando, o apontado é o primeiro excedente: é onde o comando deixou de ser
    reconhecível, e os seguintes são consequência dele, não erros independentes. Faltando,
    não há token a apontar — o que falta não está escrito —, então o que se informa é a
    contagem.
    """
    if len(arguments) > expected:
        raise ParseError(
            f"argumento a mais em {keyword}: {arguments[expected]!r}; "
            f"forma esperada: {_USAGE[keyword]}"
        )
    if len(arguments) < expected:
        raise ParseError(
            f"argumentos a menos em {keyword}: vieram {len(arguments)} de {expected}; "
            f"forma esperada: {_USAGE[keyword]}"
        )


def _expect_literal(keyword: str, token: str, expected: str) -> None:
    """Confere as palavras fixas da gramática (``cancel ORDER``, ``print BOOK``)."""
    if token.lower() != expected:
        raise ParseError(
            f"esperado {expected!r} e não {token!r}; forma esperada: {_USAGE[keyword]}"
        )


def _parse_side(token: str) -> Side:
    match token.lower():
        case "buy":
            return Side.BUY
        case "sell":
            return Side.SELL
        case _:
            raise ParseError(f"lado inválido: {token!r}; esperado buy ou sell")


def _parse_peg_reference(token: str) -> PegReference:
    match token.lower():
        case "bid":
            return PegReference.BID
        case "offer":
            return PegReference.OFFER
        case _:
            raise ParseError(f"referência de peg inválida: {token!r}; esperado bid ou offer")


def _parse_price(token: str) -> Ticks:
    """Delega a ``parse_price`` e reembala a recusa como ``ParseError``.

    A mensagem original é preservada porque ela é mais específica do que qualquer coisa
    que o parser saberia dizer: distingue preço malformado de preço mais preciso que o
    tick e de preço zero. O que muda é só o tipo, para que quem chama ``parse`` tenha uma
    exceção só a tratar em vez de uma por conversor usado lá dentro.
    """
    try:
        return parse_price(token)
    except InvalidPriceError as error:
        raise ParseError(str(error)) from error


def _parse_quantity(token: str) -> int:
    quantity = _positive_integer(token)
    if quantity is None:
        raise ParseError(f"quantidade inválida: {token!r}; esperado inteiro maior que zero")
    return quantity


def _parse_order_id(token: str) -> OrderId:
    order_id = _positive_integer(token)
    if order_id is None:
        raise ParseError(f"id de ordem inválido: {token!r}; esperado inteiro maior que zero")
    return OrderId(order_id)


def _positive_integer(token: str) -> int | None:
    """Regra única para quantidade e id; ``None`` quando o token não a satisfaz.

    A conversão é feita por regex antes de ``int``, e não por ``try``/``except``, porque
    ``int`` é permissivo onde a gramática não é: aceita sinal, espaços em volta e
    sublinhados (``"1_0"`` vira 10). Só dígitos passam, e ``"10.5"`` e ``"-5"`` são
    recusados por não casarem, não por darem erro de conversão.

    Quem devolve a mensagem é o chamador, porque só ele sabe se o token era quantidade ou
    id — e uma mensagem que não nomeia o campo obriga o usuário a adivinhar qual dos
    números da linha está errado.
    """
    if _DIGITS.fullmatch(token) is None:
        return None
    value = int(token)
    return value if value > 0 else None
