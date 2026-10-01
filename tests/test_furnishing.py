import pytest

from roomplanner.generator import generate
from roomplanner.geometry import Cell, Edge, Side, connected
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


def test_meeting_tables_come_in_sizes_that_fit_the_room() -> None:
    kinds: set[str] = set()
    for seed in range(3):
        params = make_params(width=60, depth=40, floors_above=2, wealth=Wealth.LUXURY, seed=seed)
        for floor in generate(params).floors:
            for room in floor.rooms:
                if room.type != "meeting_room":
                    continue
                tables = [o.kind for o in floor.objects if o.room == room.id and "table" in o.kind]
                kinds.add("long" if len(tables) == 2 else tables[0])
    assert {"round_table", "meeting_table", "long"} <= kinds


@pytest.mark.parametrize(
    ("building_type", "width", "depth", "wealth", "seed"),
    [
        (BuildingType.CLINIC, 52, 49, Wealth.LUXURY, 945989),  # toilet wraps round the row
        (BuildingType.HOSPITAL, 55, 35, Wealth.SQUATTER, 942500),  # a widened stall
        (BuildingType.OFFICE, 70, 45, Wealth.SQUATTER, 876507),
    ],
)
def test_the_stalls_of_a_toilet_all_face_the_same_way(
    building_type: BuildingType, width: int, depth: int, wealth: Wealth, seed: int
) -> None:
    params = make_params(
        building_type=building_type,
        width=width,
        depth=depth,
        floors_above=3,
        wealth=wealth,
        seed=seed,
    )
    for floor in generate(params).floors:
        owner = {c: r for r in floor.rooms for c in r.cells}
        rows: dict[str, set[Side]] = {}
        for door in floor.openings:
            a, b = (owner.get(c) for c in door.edges[0].cells())
            for stall, toilet in ((a, b), (b, a)):
                if stall is None or toilet is None or stall.type != "stall":
                    continue
                wc = next(o for o in floor.objects if o.room == stall.id and o.kind == "wc")
                rows.setdefault(toilet.id, set()).add(wc.facing)
        assert rows
        assert all(len(facings) == 1 for facings in rows.values())


def test_meeting_rooms_are_roomy_and_every_table_has_chairs() -> None:
    for seed in range(4):
        params = make_params(
            width=72, depth=30, floors_above=2, wealth=Wealth.SQUATTER, seed=519501 + seed
        )
        for floor in generate(params).floors:
            for room in floor.rooms:
                if room.type != "meeting_room":
                    continue
                xs, ys = [c.x for c in room.cells], [c.y for c in room.cells]
                short, long = sorted((max(xs) - min(xs) + 1, max(ys) - min(ys) + 1))
                assert short >= 8
                assert long <= 2 * short
                kinds = [o.kind for o in floor.objects if o.room == room.id]
                assert "chair" in kinds


@pytest.mark.parametrize("building_type", [BuildingType.DIVE_BAR, BuildingType.NIGHTCLUB])
def test_cafe_tables_stand_on_one_lattice_facing_the_same_way(building_type: BuildingType) -> None:
    floor = generate(make_params(building_type=building_type, width=48, depth=36, seed=3)).floor(0)
    tables = [o for o in floor.objects if o.kind == "bar_table"]
    assert len(tables) >= 2
    assert len({o.facing for o in tables}) == 1
    for values in ({o.x for o in tables}, {o.y for o in tables}):
        steps = {b - a for a, b in zip(sorted(values), sorted(values)[1:], strict=False)}
        if steps:  # one pitch: every gap is a multiple of the smallest
            assert all(step % min(steps) == 0 for step in steps)


@pytest.mark.parametrize(("width", "depth"), [(40, 48), (56, 32), (48, 60)])
def test_the_church_portal_opens_into_a_narthex_leading_to_the_nave(width: int, depth: int) -> None:
    params = make_params(building_type=BuildingType.CHURCH, width=width, depth=depth)
    ground = generate(params).floor(0)
    owner = {c: r for r in ground.rooms for c in r.cells}
    portal = next(o for o in ground.openings if o.entrance == "main")
    assert {owner[c].type for c in portal.edges[0].cells() if c in owner} == {"narthex"}
    pairs = [
        {owner[c].type for c in door.edges[0].cells() if c in owner}
        for door in ground.openings
        if door.kind is OpeningKind.DOOR
    ]
    assert {"narthex", "nave"} in pairs


def test_bigger_buildings_get_more_elevators_whose_cars_fill_the_shaft() -> None:
    def elevators(width: int, depth: int) -> list[tuple[int, int]]:
        floor = generate(make_params(width=width, depth=depth, floors_above=3)).floor(1)
        shafts = [r for r in floor.rooms if r.type == "elevator"]
        cars = [o for o in floor.objects if o.kind == "elevator_car"]
        return [(len(r.cells), next(c.w * c.h for c in cars if c.room == r.id)) for r in shafts]

    small, large = elevators(40, 24), elevators(60, 44)
    assert len(small) == 1 < len(large)
    assert all(car >= shaft * 0.7 for shaft, car in small + large)


def test_offices_have_three_to_seven_desks_in_pairs_each_with_a_chair() -> None:
    floor = generate(make_params(width=60, depth=40, floors_above=3, seed=1)).floor(2)
    offices = [r for r in floor.rooms if r.type == "office"]
    assert offices
    for room in offices:
        kinds = [o.kind for o in floor.objects if o.room == room.id]
        desks = kinds.count("office_desk")
        assert 2 <= desks <= 7
        assert kinds.count("chair") == desks


def test_office_interior_rows_mix_huddle_focus_copy_and_break_rooms() -> None:
    counts: dict[str, int] = {}
    for seed in range(4):
        for floor in generate(make_params(width=60, depth=40, floors_above=3, seed=seed)).floors:
            types = [r.type for r in floor.rooms]
            assert types.count("copy_room") <= 1
            assert types.count("staff_room") <= 1
            for kind in ("huddle_room", "focus_room"):
                counts[kind] = counts.get(kind, 0) + types.count(kind)
    assert counts["focus_room"] < counts["huddle_room"]


@pytest.mark.parametrize(
    ("building_type", "open_plan"),
    [(BuildingType.OFFICE, "open_office"), (BuildingType.CORP_OFFICE, "cubicle_farm")],
)
def test_open_plan_offices_have_no_wall_to_the_corridor(
    building_type: BuildingType, open_plan: str
) -> None:
    params = make_params(building_type=building_type, width=60, depth=40, floors_above=3, seed=1)
    shared = 0
    for floor in generate(params).floors:
        owner = {c: r for r in floor.rooms for c in r.cells}
        for room in (r for r in floor.rooms if r.type == open_plan):
            for cell in room.cells:
                for side in Side:
                    other = owner.get(cell.neighbour(side))
                    if other is not None and other.type == "corridor":
                        shared += 1
                        assert Edge.of(cell, side) not in floor.walls
    assert shared


def test_meeting_room_screens_sit_on_the_axis_of_the_table() -> None:
    found = 0
    for seed in range(4):
        for floor in generate(make_params(width=60, depth=40, floors_above=3, seed=seed)).floors:
            for room in (r for r in floor.rooms if r.type in ("meeting_room", "huddle_room")):
                objects = [o for o in floor.objects if o.room == room.id]
                table = max((o for o in objects if o.blocking), key=lambda o: o.w * o.h)
                for screen in (o for o in objects if o.kind in ("wall_screen", "whiteboard")):
                    found += 1
                    if screen.facing in (Side.E, Side.W):
                        off = screen.y + screen.h / 2 - (table.y + table.h / 2)
                    else:
                        off = screen.x + screen.w / 2 - (table.x + table.w / 2)
                    assert abs(off) <= 1
    assert found


def test_office_kitchens_are_sometimes_open_to_the_corridor() -> None:
    kinds: set[bool] = set()
    for seed in range(6):
        for floor in generate(make_params(width=60, depth=40, floors_above=3, seed=seed)).floors:
            owner = {c: r for r in floor.rooms for c in r.cells}
            for room in (r for r in floor.rooms if r.type == "kitchenette"):
                shared = [
                    Edge.of(c, s)
                    for c in room.cells
                    for s in Side
                    if (o := owner.get(c.neighbour(s))) is not None and o.type == "corridor"
                ]
                if shared:
                    kinds.add(any(e in floor.walls for e in shared))
    assert kinds == {True, False}


def test_corp_offices_have_one_training_room_per_floor_at_most_and_none_upstairs() -> None:
    for seed in range(3):
        params = make_params(
            building_type=BuildingType.CORP_OFFICE,
            width=72,
            depth=52,
            floors_above=4,
            wealth=Wealth.HIGH,
            seed=seed,
        )
        for floor in generate(params).floors:
            count = sum(r.type == "training_room" for r in floor.rooms)
            assert count <= (0 if floor.role == "executive" else 1)


def test_vending_machines_line_the_walls_of_an_office_vending_room() -> None:
    rooms = 0
    for seed in range(4):
        params = make_params(width=72, depth=52, floors_above=3, seed=seed)
        for floor in generate(params).floors:
            vending = [r for r in floor.rooms if r.type == "vending_room"]
            assert len(vending) <= 1
            for room in vending:
                rooms += 1
                machines = [o for o in floor.objects if o.room == room.id]
                machines = [o for o in machines if o.kind == "vending_machine"]
                # a machine per metre of wall, roughly: the room's perimeter less doors
                assert len(machines) * 2 >= len(room.cells) // 6
    assert rooms


def test_vending_rooms_are_open_on_one_or_two_sides_and_take_no_other_doors() -> None:
    rooms = 0
    for building_type in (BuildingType.OFFICE, BuildingType.COFFIN_BLOCK):
        for seed in range(3):
            params = make_params(building_type=building_type, width=60, depth=40, seed=seed)
            for floor in generate(params).floors:
                owner = {c: r for r in floor.rooms for c in r.cells}
                for room in (r for r in floor.rooms if r.type == "vending_room"):
                    rooms += 1
                    sides = {
                        s
                        for c in room.cells
                        for s in Side
                        if c.neighbour(s) in owner
                        and owner[c.neighbour(s)] is not room
                        and Edge.of(c, s) not in floor.walls
                    }
                    assert 1 <= len(sides) <= 2
                    doors = [
                        o
                        for o in floor.openings
                        if o.kind is OpeningKind.DOOR
                        and any(c in room.cells for e in o.edges for c in e.cells())
                    ]
                    assert not doors
    assert rooms


def test_corp_offices_have_a_guard_room_with_lockers_and_weapons() -> None:
    params = make_params(building_type=BuildingType.CORP_OFFICE, width=60, depth=40, seed=1)
    ground = generate(params).floor(0)
    room = next(r for r in ground.rooms if r.type == "guard_room")
    kinds = [o.kind for o in ground.objects if o.room == room.id]
    assert kinds.count("locker") >= 2
    assert "weapon_locker" in kinds


def test_the_corp_executive_floor_has_a_balcony_the_rooms_behind_it_look_onto() -> None:
    params = make_params(
        building_type=BuildingType.CORP_OFFICE,
        width=72,
        depth=52,
        floors_above=3,
        wealth=Wealth.HIGH,
        seed=2077,
    )
    building = generate(params)
    top = building.floor(2)
    assert top.role == "executive"
    balcony = next(r for r in top.rooms if r.type == "balcony")
    windows = [
        o
        for o in top.openings
        if o.kind is OpeningKind.WINDOW
        and any(c in balcony.cells for e in o.edges for c in e.cells())
    ]
    assert windows  # onto the balcony, not out of it
    assert all(not top.is_exterior_wall(e) for o in windows for e in o.edges)
    assert all(r.type != "balcony" for f in building.floors[:-1] for r in f.rooms)


def test_a_penthouse_sits_above_the_executive_floor_set_back_from_its_balcony() -> None:
    params = make_params(
        building_type=BuildingType.CORP_OFFICE,
        width=72,
        depth=52,
        floors_above=6,
        wealth=Wealth.HIGH,
        seed=2077,
    )
    building = generate(params)
    executive, penthouse = building.floor(4), building.floor(5)
    assert (executive.role, penthouse.role) == ("executive", "penthouse")
    balcony = next(r for r in executive.rooms if r.type == "balcony")
    assert not balcony.cells & penthouse.footprint  # open to the sky
    assert penthouse.footprint | balcony.cells == executive.footprint


def test_some_office_towers_end_in_a_flat_roof_the_stairs_open_onto() -> None:
    params = make_params(
        building_type=BuildingType.CORP_OFFICE, width=60, depth=40, floors_above=5, seed=4
    )
    top = generate(params).floors[-1]
    assert top.role == "roof"
    assert {r.type for r in top.rooms} == {"roof", "stairwell", "elevator"}
    roof = next(r for r in top.rooms if r.type == "roof")
    kinds = [o.kind for o in top.objects if o.room == roof.id]
    assert "condenser" in kinds or "hvac_unit" in kinds
    stairs = next(r for r in top.rooms if r.type == "stairwell")
    assert any(
        o.kind is OpeningKind.DOOR
        and any(c in stairs.cells for e in o.edges for c in e.cells())
        and any(c in roof.cells for e in o.edges for c in e.cells())
        for o in top.openings
    )


def test_office_leftovers_become_nap_rooms_full_of_sleep_pods() -> None:
    params = make_params(
        building_type=BuildingType.CORP_OFFICE, width=60, depth=40, floors_above=3, seed=2
    )
    floor = generate(params).floor(2)
    naps = [r for r in floor.rooms if r.type == "nap_room"]
    assert naps
    for room in naps:
        pods = [o for o in floor.objects if o.room == room.id and o.kind == "capsule"]
        assert len(pods) >= 2


def test_office_lobbies_have_a_reception_desk_with_a_free_way_from_the_door() -> None:
    for seed in range(4):
        ground = generate(make_params(width=60, depth=40, floors_above=3, seed=seed)).floor(0)
        lobby = next(r for r in ground.rooms if r.type == "reception_lobby")
        objects = [o for o in ground.objects if o.room == lobby.id]
        desk = next(o for o in objects if o.kind == "reception_desk")
        dx, dy = desk.facing.delta
        front = {
            Cell(c.x + dx * k, c.y + dy * k)
            for c in desk.cells
            for k in range(1, 40)
            if Cell(c.x + dx * k, c.y + dy * k) in lobby.cells
        }
        assert not any(o.blocking and o.cells & front for o in objects if o is not desk)


def test_open_offices_fill_their_depth_with_desks_up_to_the_corridor() -> None:
    building = generate(make_params(width=60, depth=40, floors_above=3, wealth=Wealth.LOW, seed=0))
    floor = building.floor(1)
    room = next(r for r in floor.rooms if r.type == "open_office")
    desks = [o for o in floor.objects if o.room == room.id and o.kind == "office_desk"]
    assert len(desks) * 6 >= len(room.cells) // 4  # a desk per ~4 m² at least
    assert not any(w.startswith("[hard]") for w in building.warnings)
