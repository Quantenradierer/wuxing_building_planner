import pytest

from roomplanner.geometry import Axis, Edge, Side
from roomplanner.model import Opening, OpeningKind, Swing

RUN = (Edge(0, 0, Axis.H), Edge(1, 0, Axis.H))


def test_valid_door() -> None:
    door = Opening(OpeningKind.DOOR, RUN, Swing(towards=Side.S, hinge=Side.W))
    assert door.axis is Axis.H


@pytest.mark.parametrize(
    ("swing", "message"),
    [
        (Swing(towards=Side.E, hinge=Side.W), "perpendicular"),
        (Swing(towards=Side.S, hinge=Side.N), "hinge"),
    ],
)
def test_door_swing_must_fit_its_wall(swing: Swing, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Opening(OpeningKind.DOOR, RUN, swing)


def test_door_needs_swing_and_window_must_not_have_one() -> None:
    with pytest.raises(ValueError, match="need a swing"):
        Opening(OpeningKind.DOOR, RUN)
    with pytest.raises(ValueError, match="no swing"):
        Opening(OpeningKind.WINDOW, RUN, Swing(Side.S, Side.W))


@pytest.mark.parametrize(
    "edges",
    [
        (Edge(0, 0, Axis.H), Edge(2, 0, Axis.H)),  # gap
        (Edge(0, 0, Axis.H), Edge(0, 0, Axis.V)),  # bend
        (Edge(1, 0, Axis.H), Edge(0, 0, Axis.H)),  # unsorted
    ],
)
def test_opening_edges_must_be_a_sorted_straight_run(edges: tuple[Edge, ...]) -> None:
    with pytest.raises(ValueError, match="opening edges"):
        Opening(OpeningKind.WINDOW, edges)


def test_only_doors_slide() -> None:
    assert Opening(OpeningKind.DOOR, RUN, Swing(Side.S, Side.W), sliding=True).sliding
    with pytest.raises(ValueError, match="do not slide"):
        Opening(OpeningKind.WINDOW, RUN, sliding=True)
