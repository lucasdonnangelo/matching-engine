import pytest

from matching_engine.domain.side import PegReference, Side


@pytest.mark.parametrize(
    ("side", "expected"),
    [(Side.BUY, Side.SELL), (Side.SELL, Side.BUY)],
)
def test_opposite(side: Side, expected: Side) -> None:
    assert side.opposite is expected


@pytest.mark.parametrize("side", list(Side))
def test_opposite_of_opposite_is_the_side_itself(side: Side) -> None:
    assert side.opposite.opposite is side


@pytest.mark.parametrize(
    ("reference", "expected"),
    [(PegReference.BID, Side.BUY), (PegReference.OFFER, Side.SELL)],
)
def test_peg_reference_points_at_the_top_of_its_side(
    reference: PegReference, expected: Side
) -> None:
    assert reference.side is expected
