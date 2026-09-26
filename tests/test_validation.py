from dataclasses import replace

from roomplanner.generator import generate
from roomplanner.geometry import Cell, Edge, Side
from roomplanner.model import Building, Floor, OpeningKind, PlacedObject, Room
from roomplanner.rules import rules_for
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
    objects = tuple(o for o in ground.objects if closet.isdisjoint(o.cells))
    broken = replace(ground, rooms=rooms, walls=walls, objects=objects)
    assert messages(with_ground_floor(building, broken)) == [
        "1 cells unreachable, e.g. Cell(x=0, y=0)"
    ]


def test_detects_missing_entrance() -> None:
    building = generate(make_params())
    ground = building.floor(0)
    broken = replace(ground, openings=())
    assert "ground floor has no exterior door" in messages(with_ground_floor(building, broken))


def test_detects_misaligned_core() -> None:
    params = make_params(floors_above=2)
    building = generate(params)
    upper = building.floor(1)
    stairs = next(r for r in upper.rooms if r.type == "stairwell")
    other = next(r for r in upper.rooms if r.type not in ("stairwell", "elevator", "corridor"))
    moved_cell = min(other.cells)
    rooms = replace_room(
        upper,
        stairs,
        replace(stairs, cells=stairs.cells | {moved_cell}),
    )
    rooms = tuple(
        replace(r, cells=r.cells - {moved_cell}) if r.id == other.id else r for r in rooms
    )
    broken = replace(building, floors=(building.floor(0), replace(upper, rooms=rooms)))
    rules = rules_for(params.building_type, params.wealth)
    assert any("stairwell not aligned" in v.message for v in hard_violations(broken, rules))


def test_detects_core_door_moved_between_floors() -> None:
    params = make_params(floors_above=2)
    building = generate(params)
    upper = building.floor(1)
    stairs = next(r for r in upper.rooms if r.type == "stairwell")
    door = next(
        o
        for o in upper.openings
        if o.kind is OpeningKind.DOOR and any(c in stairs.cells for c in o.edges[0].cells())
    )
    shifted = replace(door, edges=(*door.edges[1:], door.edges[-1].next_along()))
    openings = tuple(shifted if o == door else o for o in upper.openings)
    broken = replace(building, floors=(building.floor(0), replace(upper, openings=openings)))
    rules = rules_for(params.building_type, params.wealth)
    assert not any("door moved" in v.message for v in hard_violations(building, rules))
    assert any("stairwell door moved" in v.message for v in hard_violations(broken, rules))


def test_detects_rooms_below_minimum_width() -> None:
    params = make_params()
    building = generate(params)
    ground = building.floor(0)
    office = next(r for r in ground.rooms if r.type == "office")
    column = min(c.x for c in office.cells)
    sliver = frozenset(c for c in office.cells if c.x == column)
    rooms = replace_room(
        ground, office, replace(office, cells=office.cells - sliver), Room("0.99", "office", sliver)
    )
    broken = with_ground_floor(building, replace(ground, rooms=rooms))
    rules = rules_for(params.building_type, params.wealth)
    assert any("1 cells wide" in v.message for v in hard_violations(broken, rules))


def test_detects_objects_blocking_doors_and_overlapping() -> None:
    building = generate(make_params())
    ground = building.floor(0)
    room = next(r for r in ground.rooms if ground.door_clearance(r) and r.type == "office")
    cell = min(ground.door_clearance(room))
    blocker = PlacedObject("pallet", cell.x, cell.y, 1, 1, Side.S, room.id)
    broken = with_ground_floor(building, replace(ground, objects=(blocker, blocker)))
    found = messages(broken)
    assert any("block a door" in m for m in found)
    assert any("overlaps another object" in m for m in found)


def test_detects_objects_outside_their_room() -> None:
    building = generate(make_params())
    ground = building.floor(0)
    stray = PlacedObject("pallet", -5, -5, 1, 1, Side.S, ground.rooms[0].id)
    broken = with_ground_floor(building, replace(ground, objects=(stray,)))
    assert any("outside room" in m for m in messages(broken))
