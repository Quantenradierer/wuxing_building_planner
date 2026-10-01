"""Cluster hallways end after their last door; the end joins a room beside it."""

from roomplanner.geometry import Cell
from roomplanner.params import BuildingType, Wealth
from roomplanner.pipeline.base import PlannedRoom
from roomplanner.pipeline.layout.hallways import trim_hallways
from roomplanner.rules import rules_for

RULES = rules_for(BuildingType.OFFICE, Wealth.MIDDLE)


def _rect(x0: int, y0: int, x1: int, y1: int) -> frozenset[Cell]:
    return frozenset(Cell(x, y) for x in range(x0, x1) for y in range(y0, y1))


# A corridor along the top, a hallway down from it beside a column of two offices.
CORRIDOR = PlannedRoom("corridor", _rect(0, 0, 20, 3))
HALLWAY = PlannedRoom("corridor", _rect(0, 3, 3, 23), hallway=True)
FRONT = PlannedRoom("office", _rect(3, 3, 10, 13))
BACK = PlannedRoom("office", _rect(3, 13, 10, 23))


def _by_type(rooms: list[PlannedRoom]) -> dict[str, list[frozenset[Cell]]]:
    found: dict[str, list[frozenset[Cell]]] = {}
    for room in rooms:
        found.setdefault(room.type, []).append(room.cells)
    return found


def test_hallway_ends_after_the_last_door_and_the_room_wraps_round_its_end() -> None:
    rooms = trim_hallways([CORRIDOR, HALLWAY, FRONT, BACK], RULES)
    hallway = next(r for r in rooms if r.hallway)
    # The back office keeps its door and a cell either side along the hallway.
    assert hallway.cells == _rect(0, 3, 3, 17)
    back = next(r for r in rooms if r.type == "office" and Cell(5, 20) in r.cells)
    assert back.cells == BACK.cells | _rect(0, 17, 3, 23)
    assert len([r for r in rooms if r.type == "office"]) == 2


def test_exterior_door_spot_stays_hallway() -> None:
    rooms = trim_hallways([CORRIDOR, HALLWAY, FRONT, BACK], RULES, frozenset({Cell(1, 22)}))
    assert rooms == [CORRIDOR, HALLWAY, FRONT, BACK]


def test_hallway_nobody_needs_joins_a_room() -> None:
    """Rooms that open onto the corridor directly don't keep a hallway beside them."""
    room = PlannedRoom("office", _rect(3, 3, 10, 13))
    hallway = PlannedRoom("corridor", _rect(0, 3, 3, 13), hallway=True)
    rooms = trim_hallways([CORRIDOR, hallway, room], RULES)
    assert _by_type(rooms) == {"corridor": [CORRIDOR.cells], "office": [_rect(0, 3, 10, 13)]}


def test_thin_end_stays_hallway() -> None:
    """One or two rows past the last door are left as they are."""
    hallway = PlannedRoom("corridor", _rect(0, 3, 3, 19), hallway=True)
    back = PlannedRoom("office", _rect(3, 13, 10, 19))
    rooms = [CORRIDOR, hallway, FRONT, back]
    assert trim_hallways(rooms, RULES) == rooms
