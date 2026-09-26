from roomplanner.generator import generate
from roomplanner.geometry import Cell, connected
from roomplanner.model import Building, OpeningKind
from roomplanner.params import BuildingType, Wealth
from roomplanner.pipeline.furnishing import ring_is_one_run
from roomplanner.serialization import from_json, to_json

from .conftest import make_params


def objects_of(building: Building, kind: str) -> int:
    return sum(o.kind == kind for f in building.floors for o in f.objects)


def test_objects_stay_in_their_rooms_and_keep_doors_clear() -> None:
    building = generate(make_params(width=60, depth=40, floors_above=2))
    for floor in building.floors:
        rooms = {r.id: r for r in floor.rooms}
        for obj in floor.objects:
            room = rooms[obj.room]
            assert obj.cells <= room.cells
            if obj.blocking:
                assert not obj.cells & floor.door_clearance(room)


def test_stairwells_have_stairs_that_can_be_walked_over() -> None:
    building = generate(make_params(width=60, depth=40, floors_above=3))
    for floor in building.floors:
        stairwells = {r.id for r in floor.rooms if r.type == "stairwell"}
        stairs = [o for o in floor.objects if o.kind == "stairs"]
        assert {o.room for o in stairs} == stairwells
        assert not any(o.blocking for o in stairs)
    assert from_json(to_json(building)) == building


def test_furnished_rooms_stay_walkable() -> None:
    building = generate(make_params(building_type=BuildingType.SUPERMARKET, width=80, depth=50))
    floor = building.floor(0)
    for room in floor.rooms:
        taken = {c for o in floor.objects if o.room == room.id and o.blocking for c in o.cells}
        if taken:
            assert connected(room.cells - taken)


def test_supermarket_has_shelf_rows_and_checkouts_near_the_entrance() -> None:
    building = generate(make_params(building_type=BuildingType.SUPERMARKET, width=80, depth=50))
    assert objects_of(building, "gondola") >= 6
    ground = building.floor(0)
    checkouts = [o for o in ground.objects if o.kind == "checkout"]
    assert checkouts
    main_door = next(
        o
        for o in ground.openings
        if o.kind is OpeningKind.DOOR and ground.is_exterior_wall(o.edges[0]) and len(o.edges) == 6
    )
    door_x, door_y = main_door.edges[0].x, main_door.edges[0].y
    nearest = min(abs(c.x - door_x) + abs(c.y - door_y) for c in checkouts[0].cells)
    assert nearest < 15


def test_wealth_scales_furniture_per_room() -> None:
    def per_office(wealth: Wealth) -> float:
        offices = objects = 0
        for seed in range(3):
            for floor in generate(make_params(width=60, depth=40, wealth=wealth, seed=seed)).floors:
                ids = {r.id for r in floor.rooms if r.type == "office"}
                offices += len(ids)
                objects += sum(o.room in ids for o in floor.objects)
        return objects / offices

    assert per_office(Wealth.SQUATTER) < per_office(Wealth.LUXURY)


def test_objects_survive_json_round_trip() -> None:
    building = generate(make_params(building_type=BuildingType.APARTMENT, width=48, depth=34))
    assert objects_of(building, "bed") > 0
    assert from_json(to_json(building)) == building


def test_ring_shortcut() -> None:
    free = {Cell(x, y) for x in range(5) for y in range(5)}
    # A 1x1 object in the middle of an open room cannot split it.
    assert ring_is_one_run(free - {Cell(2, 2)}, 2, 2, 1, 1)
    # A wall-to-wall bar splits the free cells into two runs on its ring.
    bar = {Cell(x, 2) for x in range(5)}
    assert not ring_is_one_run(free - bar, 0, 2, 5, 1)
