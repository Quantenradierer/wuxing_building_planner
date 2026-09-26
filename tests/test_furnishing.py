import pytest

from roomplanner.generator import generate
from roomplanner.geometry import Cell, Side, connected
from roomplanner.model import Building, OpeningKind
from roomplanner.params import BuildingType, Wealth
from roomplanner.pipeline.furnishing import group_parts, ring_is_one_run
from roomplanner.rules import load_groups, load_objects
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


def test_chairs_stand_at_desks_and_meeting_tables() -> None:
    building = generate(make_params(width=60, depth=40, floors_above=2))
    for floor in building.floors:
        for chair in (o for o in floor.objects if o.kind == "chair"):
            beside = [
                o
                for o in floor.objects
                if o.room == chair.room
                and (o.kind.endswith(("desk", "table")) or o.kind == "bar_counter")
                and any(c.neighbour(s) in o.cells for c in chair.cells for s in Side)
            ]
            assert beside, chair
    assert objects_of(building, "chair") >= objects_of(building, "office_desk") > 0


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


def test_exact_counts_do_not_scale_with_wealth() -> None:
    building = generate(make_params(width=56, depth=36, floors_above=2, wealth=Wealth.LUXURY))
    for floor in building.floors:
        assert sum(o.kind == "stairs" for o in floor.objects) == 1


@pytest.mark.parametrize("facing", list(Side))
def test_group_parts_turn_with_the_group(facing: Side) -> None:
    groups, objects = load_groups(), load_objects()
    for group in groups.values():
        along, deep = group.size
        w, h = (along, deep) if facing in (Side.N, Side.S) else (deep, along)
        parts = group_parts(group, objects, (10, 20, w, h, facing))
        cells: set[Cell] = set()
        for x, y, pw, ph, _ in parts:
            part = {Cell(cx, cy) for cx in range(x, x + pw) for cy in range(y, y + ph)}
            assert all(10 <= c.x < 10 + w and 20 <= c.y < 20 + h for c in part)
            assert not part & cells
            cells |= part
    _, chair = group_parts(groups["workstation"], objects, (0, 0, 3, 3, facing))
    # The chair sits on the desk's front side and faces it.
    assert chair[4] is facing.opposite


def test_groups_keep_their_parts_together() -> None:
    building = generate(make_params(building_type=BuildingType.HOTEL, width=56, depth=36))
    for floor in building.floors:
        for chair in (o for o in floor.objects if o.kind == "chair"):
            assert any(
                o.room == chair.room
                and o.kind != "chair"
                and any(c.neighbour(s) in o.cells for c in chair.cells for s in Side)
                for o in floor.objects
            ), chair


def test_rooms_can_shift_the_look_of_their_objects() -> None:
    params = make_params(width=60, depth=40, floors_above=3, wealth=Wealth.HIGH, seed=2)
    building = generate(params)
    looks: dict[str, set[Wealth | None]] = {}
    for floor in building.floors:
        types = {r.id: r.type for r in floor.rooms}
        for obj in floor.objects:
            looks.setdefault(types[obj.room], set()).add(obj.wealth)
    assert looks["office"] == {None}  # the building's own tier
    assert looks["executive_office"] == {Wealth.LUXURY}
    assert from_json(to_json(building)) == building


@pytest.mark.parametrize(("width", "depth"), [(40, 48), (56, 32), (48, 60)])
def test_pews_face_the_altar(width: int, depth: int) -> None:
    for seed in range(4):
        params = make_params(building_type=BuildingType.CHURCH, width=width, depth=depth, seed=seed)
        ground = generate(params).floor(0)
        altar = next(o for o in ground.objects if o.kind == "altar")
        pews = [o for o in ground.objects if o.kind == "pew"]
        assert pews
        assert {p.facing for p in pews} == {altar.facing.opposite}


@pytest.mark.parametrize(("width", "depth"), [(40, 48), (56, 32), (48, 60)])
def test_altar_on_a_short_wall_and_a_central_aisle_between_the_pews(width: int, depth: int) -> None:
    for seed in range(4):
        params = make_params(building_type=BuildingType.CHURCH, width=width, depth=depth, seed=seed)
        ground = generate(params).floor(0)
        nave = next(r for r in ground.rooms if r.type == "nave")
        xs, ys = [c.x for c in nave.cells], [c.y for c in nave.cells]
        wide = max(xs) - min(xs) >= max(ys) - min(ys)
        altar = next(o for o in ground.objects if o.kind == "altar")
        assert (altar.facing in (Side.E, Side.W)) == wide
        pews = [o for o in ground.objects if o.kind == "pew"]
        # Wide enough for two 8-cell pews: the aisle runs through the middle to the altar.
        across = (max(ys) - min(ys) + 1) if wide else (max(xs) - min(xs) + 1)
        if across < 2 * 8 + 2 + 2 * 3:
            continue
        if wide:
            middle = (min(ys) + max(ys) + 1) // 2
            assert all(not (p.y <= middle < p.y + p.h) for p in pews)
        else:
            middle = (min(xs) + max(xs) + 1) // 2
            assert all(not (p.x <= middle < p.x + p.w) for p in pews)


def test_a_small_stuffer_shack_has_more_than_one_row_of_gondolas() -> None:
    for seed in range(1, 4):
        params = make_params(
            building_type=BuildingType.STUFFER_SHACK, width=24, depth=16, seed=seed
        )
        ground = generate(params).floor(0)
        gondolas = [o for o in ground.objects if o.kind == "gondola"]
        assert len({o.y if o.w > o.h else o.x for o in gondolas}) >= 2
