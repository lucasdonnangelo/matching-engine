"""Lados de mercado e referências de peg.

Sem conversão de texto: mapear ``"buy" -> Side.BUY`` é responsabilidade da camada de
I/O. O domínio só conhece os valores já validados.
"""

from __future__ import annotations

from enum import Enum


class Side(Enum):
    """Lado de uma ordem no livro."""

    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> Side:
        """Lado contra o qual esta ordem procura contraparte."""
        return Side.SELL if self is Side.BUY else Side.BUY


class PegReference(Enum):
    """Topo do livro ao qual uma ordem pegged se ancora."""

    BID = "BID"
    OFFER = "OFFER"

    @property
    def side(self) -> Side:
        """Lado do livro cujo topo esta referência acompanha."""
        return Side.BUY if self is PegReference.BID else Side.SELL
