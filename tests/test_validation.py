from dataclasses import replace

from roomplanner.generator import generate
from roomplanner.geometry import Cell, Edge, Side
from roomplanner.model import Building, Floor, Room
from roomplanner.validation import hard_violations

from .conftest import make_params


def with_ground_floor(building: Building, floor: Floor) -> Building:
    floors = tuple(floor if f.level == 0 else f for f in building.floors)
    return replace(building, floors=floors)


def messages(building: Building) -> list[str]:
    return [v.message for v in hard_violations(building)]


def test_detects_missing_exterior_wall() -> None:
    building = generate(make_params())
    ground = building.floor(0)
    broken = replace(ground, walls=ground.walls - {Edge.of(Cell(0, 0), Side.W)})
    assert any("exterior wall missing" in m for m in messages(with_ground_floor(building, broken)))


def replace_room(floor: Floor, old: Room, *new: Room) -> tuple[Room, ...]:
    return tuple(r for r in floor.rooms if r != old) + new


def test_detects_cells_outside_every_room() -> None:
    building = generate(make_params())
    ground = building.floor(0)
    room = ground.room_at(Cell(0, 0))
    assert room is not None
    rooms = replace_room(ground, room, replace(room, cells=room.cells - {Cell(0, 0)}))
    broken = replace(ground, rooms=rooms)
    assert any("belong to no room" in m for m in messages(with_ground_floor(building, broken)))


def test_detects_unreachable_room() -> None:
    building = generate(make_params())
    ground = building.floor(0)
    closet = frozenset({Cell(0, 0)})
    room = ground.room_at(Cell(0, 0))
    assert room is not None
    rooms = replace_room(
        ground, room, replace(room, cells=room.cells - closet), Room("0.99", "closet", closet)
    )
    walls = ground.walls | {Edge.of(Cell(0, 0), Side.E), Edge.of(Cell(0, 0), Side.S)}
    broken = replace(ground, rooms=rooms, walls=walls)
    assert messages(with_ground_floor(building, broken)) == [
        "1 cells unreachable, e.g. Cell(x=0, y=0)"
    ]


def test_detects_missing_entrance() -> None:
    building = generate(make_params())
    ground = building.floor(0)
    broken = replace(ground, openings=())
    assert "ground floor has no exterior door" in messages(with_ground_floor(building, broken))
