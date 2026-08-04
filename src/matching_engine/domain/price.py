"""Preço como inteiro de ticks.

Comparar e somar preços é o núcleo do matching, e ponto flutuante binário não
representa 0.01 exatamente: ``0.1 + 0.2 != 0.3``. Num livro de ofertas esse erro não
estoura em lugar nenhum — ele apenas corrompe silenciosamente ordenação, agregação de
quantidade e preço de execução.

``decimal.Decimal`` também foi descartado: opera sob a precisão do contexto global (28
dígitos por padrão), então escalar uma entrada mais longa que isso arredonda em silêncio
e faz um sub-tick desaparecer sem erro — a mesma falha, por outro caminho. Evitá-la
exigiria gerenciar contexto decimal explicitamente a cada conversão.

Com o formato do texto validado por regex, converter para ticks é manipulação de string
seguida de aritmética de ``int``, que no Python é exata e de precisão arbitrária. Não
existe aritmética decimal nem float em nenhum ponto do sistema.
"""

from __future__ import annotations

import re
from typing import Final, NewType

PRICE_DECIMAL_PLACES: Final = 2
"""Casas decimais aceitas em um preço."""

TICKS_PER_UNIT: Final = 10**PRICE_DECIMAL_PLACES
"""Ticks em uma unidade de preço, ou seja, tick de 0.01."""

Ticks = NewType("Ticks", int)

_PRICE_FORMAT: Final = re.compile(r"\d+(\.\d+)?")


class InvalidPriceError(ValueError):
    """Preço malformado, fora do tick ou não positivo."""


def parse_price(text: str) -> Ticks:
    """Converte texto decimal em ticks.

    O formato é validado por regex antes de qualquer conversão, o que rejeita de uma só
    vez negativos, notação científica, ``"NaN"``, ``"Infinity"``, string vazia, espaços
    em volta, ``"10."`` e ``".5"``. O que passa é dígitos com no máximo um ponto, e daí
    em diante basta somar inteiros.

    A validação usa ``fullmatch``, não ``match`` com âncoras: em Python ``$`` casa também
    logo antes de um ``\\n`` final, então ``"10\\n"`` passaria — e ``int()`` ignora
    whitespace, de modo que o preço seria aceito em silêncio.
    """
    if _PRICE_FORMAT.fullmatch(text) is None:
        raise InvalidPriceError(f"preço malformado: {text!r}")

    units, _, fraction = text.partition(".")
    within_tick, beyond_tick = (
        fraction[:PRICE_DECIMAL_PLACES],
        fraction[PRICE_DECIMAL_PLACES:],
    )
    # Casas além do tick só passam se forem zeros: "10.000" é 10, não é sub-tick.
    if beyond_tick.strip("0"):
        tick = format_price(Ticks(1))
        raise InvalidPriceError(f"preço mais preciso que o tick de {tick}: {text!r}")

    ticks = int(units) * TICKS_PER_UNIT + int(within_tick.ljust(PRICE_DECIMAL_PLACES, "0"))
    if ticks == 0:
        raise InvalidPriceError(f"preço deve ser maior que zero: {text!r}")
    return Ticks(ticks)


def format_price(ticks: Ticks) -> str:
    """Converte ticks em texto decimal sem zeros à direita: 1000 -> ``"10"``.

    Os zeros à direita saem para reproduzir a saída esperada (1050 -> ``"10.5"``), e o
    ponto some junto quando a fração é inteiramente zeros (100 -> ``"1"``).
    """
    units, fraction = divmod(ticks, TICKS_PER_UNIT)
    digits = str(fraction).rjust(PRICE_DECIMAL_PLACES, "0").rstrip("0")
    return f"{units}.{digits}" if digits else str(units)
