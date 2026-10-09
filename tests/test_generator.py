import random

import pytest

from roomplanner.errors import InfeasibleError
from roomplanner.generator import generate
from roomplanner.geometry import Cell, Edge, Side
from roomplanner.model import Floor, OpeningKind, Room
from roomplanner.params import BuildingType, EntranceKind, Shape, Wealth
from roomplanner.pipeline.layout.corridor import FAR_CORE_MIN
from roomplanner.pipeline.openings import DAYLIGHT
from roomplanner.rules import rules_for
from roomplanner.validation import hard_violations, validate

from .conftest import make_params, uncached_generate


def test_same_seed_same_building() -> None:
    assert generate(make_params(seed=3)) == uncached_generate(make_params(seed=3))


def test_missing_seed_is_resolved_and_recorded() -> None:
    building = generate(make_params(seed=None))
    assert building.params.seed == building.seed
    assert uncached_generate(building.params) == building


def test_dimensions_are_cells() -> None:
    building = generate(make_params(width=45, depth=27))
    assert (building.width, building.height) == (45, 27)


def test_levels_run_from_lowest_basement_to_top() -> None:
    building = generate(make_params(floors_above=3, floors_below=2))
    assert [f.level for f in building.floors] == [-2, -1, 0, 1, 2]


def test_service_side_defaults_to_opposite_of_street() -> None:
    assert make_params(street_side=Side.E).service_side is Side.W


def test_exterior_doors_face_street_and_service_side_and_open_outwards() -> None:
    building = generate(make_params(street_side=Side.N, service_side=Side.E))
    ground = building.floor(0)
    exits = [
        o
        for o in ground.openings
        if o.kind is OpeningKind.DOOR and ground.is_exterior_wall(o.edges[0])
    ]
    by_kind = {d.entrance: d.swing.towards for d in exits if d.swing}
    assert by_kind["main"] is Side.N
    assert by_kind["service"] is Side.E
    assert "emergency" in by_kind


def test_entrances_parameter_overrides_the_building_type() -> None:
    building = generate(make_params(entrances=(EntranceKind.SERVICE,)))
    kinds = {o.entrance for o in building.floor(0).openings if o.entrance}
    assert kinds == {"main", "service"}
    assert not any(o.kind == "roof_hatch" for f in building.floors for o in f.objects)


def test_emergency_exit_is_away_from_the_main_entrance() -> None:
    building = generate(make_params(width=60, depth=30, floors_above=2))
    ground = building.floor(0)
    doors = {o.entrance: o.edges[0] for o in ground.openings if o.entrance}
    main, emergency = doors["main"], doors["emergency"]
    assert abs(main.x - emergency.x) + abs(main.y - emergency.y) >= 15


@pytest.mark.parametrize(
    ("building_type", "seed"),
    [(t, s) for t in ("warehouse", "chop_shop", "supermarket", "factory") for s in (1, 2, 3)],
)
def test_vehicle_rooms_have_their_own_roller_door(building_type: str, seed: int) -> None:
    params = make_params(building_type=BuildingType(building_type), width=48, depth=36, seed=seed)
    building = generate(params)
    rules = rules_for(params.building_type, params.wealth)
    ground = building.floor(0)
    exterior = [o for o in ground.openings if o.entrance]
    for room in ground.rooms:
        width = rules.spec(room.type).facade_door
        if width is None:
            continue
        cells = room.cells
        assert any(
            len(d.edges) >= width and any(c in cells for e in d.edges for c in e.cells())
            for d in exterior
        ), room.type


def test_service_door_prefers_the_loading_bay() -> None:
    building = generate(
        make_params(building_type=BuildingType.SUPERMARKET, width=48, depth=36, seed=1)
    )
    ground = building.floor(0)
    service = next(o for o in ground.openings if o.entrance == "service")
    rooms = {ground.room_at(c) for c in service.edges[0].cells()} - {None}
    assert [r.type for r in rooms if r is not None] == ["loading_bay"]


@pytest.mark.parametrize(
    ("building_type", "back_room"),
    [(BuildingType.APARTMENT, "drone_bay"), (BuildingType.HOTEL, "staff_room")],
)
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_back_door_opens_into_a_back_room_not_a_corridor_stub(
    building_type: BuildingType, back_room: str, seed: int
) -> None:
    """The room runs from the corridor to the service facade; no dead-end corridor."""
    building = generate(
        make_params(building_type=building_type, width=56, depth=36, street_side=Side.S, seed=seed)
    )
    ground = building.floor(0)
    service = next(o for o in ground.openings if o.entrance == "service")
    rooms = {ground.room_at(c) for c in service.edges[0].cells()} - {None}
    assert [r.type for r in rooms if r is not None] == [back_room]
    room = next(r for r in rooms if r is not None)
    corridors = {c for r in ground.rooms if r.type == "corridor" for c in r.cells}
    assert any(c.neighbour(s) in corridors for c in room.cells for s in Side)
    assert len(room.cells) <= rules_for(building_type, Wealth.MIDDLE).spec(back_room).area[1] * 1.5


@pytest.mark.parametrize(
    ("shape", "seed"), [(Shape.RECTANGLE, 1), (Shape.RECTANGLE, 2), (Shape.T, 18), (Shape.L, 5)]
)
def test_supermarket_stairs_are_open_stairs_on_the_sales_floor(shape: Shape, seed: int) -> None:
    building = generate(
        make_params(
            building_type=BuildingType.SUPERMARKET,
            width=70 if shape is Shape.T else 56,
            depth=33 if shape is Shape.T else 46,
            floors_above=2,
            floors_below=1,
            shape=shape,
            seed=seed,
        )
    )
    assert not hard_violations(building)
    for floor in building.floors:
        assert not any(r.type == "stairwell" for r in floor.rooms)
        stairs = next(r for r in floor.rooms if r.type == "public_stairs")
        hall = {"sales_floor"} if floor.level >= 0 else {"stockroom"}
        neighbours = {
            room.type
            for c in stairs.cells
            for side in Side
            if Edge.of(c, side) not in floor.walls
            and (room := floor.room_at(c.neighbour(side))) is not None
            and room is not stairs
        }
        assert neighbours and neighbours <= hall | {"corridor"}  # open to the hall
        assert any(o.kind == "stairs" and o.room == stairs.id for o in floor.objects)


def test_top_floor_stairwell_has_a_roof_hatch() -> None:
    building = generate(make_params(floors_above=3))
    top = building.floor(2)
    hatches = [o for o in top.objects if o.kind == "roof_hatch"]
    assert len(hatches) == 1
    assert top.room_at(Cell(hatches[0].x, hatches[0].y)).type == "stairwell"  # type: ignore[union-attr]
    assert not any(o.kind == "roof_hatch" for o in building.floor(1).objects)


def test_basements_have_no_windows() -> None:
    building = generate(make_params(floors_below=1))
    assert all(o.kind is OpeningKind.DOOR for o in building.floor(-1).openings)


def test_generated_building_is_valid() -> None:
    assert validate(generate(make_params(floors_above=2, floors_below=1))) == []


def test_all_building_types_generate() -> None:
    for building_type in BuildingType:
        building = generate(make_params(building_type=building_type, width=60, depth=40, seed=6))
        rules = rules_for(building_type, building.params.wealth)
        assert validate(building, rules) == []


def test_too_small_footprint_fails_fast() -> None:
    with pytest.raises(InfeasibleError, match="at least"):
        generate(make_params(width=3))


@pytest.mark.parametrize("shape", [Shape.T, Shape.Z, Shape.STEPPED, Shape.IRREGULAR])
@pytest.mark.parametrize("building_type", list(BuildingType))
def test_composite_shapes_are_valid(shape: Shape, building_type: BuildingType) -> None:
    params = make_params(building_type=building_type, shape=shape, width=96, depth=80, seed=4)
    building = generate(params)
    footprint = building.floor(0).footprint
    assert len(footprint) < 96 * 80
    assert hard_violations(building, rules_for(building_type, building.params.wealth)) == []


@pytest.mark.parametrize("shape", [Shape.T, Shape.Z, Shape.STEPPED, Shape.IRREGULAR])
def test_small_composite_shapes_explain_the_minimum(shape: Shape) -> None:
    with pytest.raises(InfeasibleError, match="too small for"):
        generate(make_params(shape=shape, width=20, depth=20))


def test_l_shape_misses_one_corner() -> None:
    building = generate(make_params(shape=Shape.L, width=60, depth=48, seed=6))
    footprint = building.floor(0).footprint
    corners = [(0, 0), (59, 0), (0, 47), (59, 47)]
    assert sum((x, y) in footprint for x, y in corners) == 3
    assert (
        validate(building, rules_for(building.params.building_type, building.params.wealth)) == []
    )


@pytest.mark.parametrize("building_type", list(BuildingType))
def test_u_shape_has_a_courtyard_notch(building_type: BuildingType) -> None:
    building = generate(
        make_params(building_type=building_type, shape=Shape.U, width=90, depth=64, seed=3)
    )
    footprint = building.floor(0).footprint
    corners = [(0, 0), (89, 0), (0, 63), (89, 63)]
    assert all(corner in footprint for corner in corners)
    assert len(footprint) < 90 * 64
    assert hard_violations(building, rules_for(building_type, building.params.wealth)) == []


def test_small_u_shape_explains_minimum() -> None:
    with pytest.raises(InfeasibleError, match="too small for a U shape"):
        generate(make_params(shape=Shape.U, width=30, depth=30))


def test_small_l_shape_explains_minimum() -> None:
    with pytest.raises(InfeasibleError, match="too small for an L shape"):
        generate(make_params(shape=Shape.L, width=30, depth=20))


def test_luxury_offices_have_bigger_rooms_than_squatter_ones() -> None:
    def mean_office(wealth: Wealth) -> float:
        building = generate(make_params(width=80, depth=40, floors_above=2, wealth=wealth))
        offices = [r.area for f in building.floors for r in f.rooms if r.type == "office"]
        return sum(offices) / len(offices)

    assert mean_office(Wealth.SQUATTER) < mean_office(Wealth.LUXURY)


def test_window_rooms_in_deep_strips_keep_their_size() -> None:
    """Exam rooms and wards in a deep hospital go into facade stacks, not full-depth slots."""
    # Seeds 0 and 34 hit the known oversized top-floor doctor's office (requirements.md).
    params = make_params(
        building_type=BuildingType.HOSPITAL, width=70, depth=44, floors_above=3, seed=1
    )
    building = generate(params)
    rules = rules_for(params.building_type, params.wealth)
    for floor in building.floors:
        for room in floor.rooms:
            if room.type in ("exam_room", "ward", "doctor_office"):
                assert len(room.cells) <= rules.spec(room.type).area[1] * 1.5, room


def test_bedrooms_get_a_window_cell_per_six_square_metres() -> None:
    """Narrow bedrooms widen their one grid window (MBO §47, window area >= 1/8 floor)."""
    params = make_params(
        building_type=BuildingType.APARTMENT,
        width=41,
        depth=46,
        floors_above=5,
        wealth=Wealth.MIDDLE,
        seed=7104,
    )
    bedrooms = 0
    for floor in generate(params).floors:
        windows = [o for o in floor.openings if o.kind is OpeningKind.WINDOW]
        for room in floor.rooms:
            if room.type == "bedroom":
                bedrooms += 1
                cells = sum(len(w.edges) for w in windows if set(w.edges[0].cells()) & room.cells)
                assert cells * DAYLIGHT >= room.area, room
    assert bedrooms > 10


def test_kitchen_opens_straight_into_the_restaurant() -> None:
    linked = 0
    for seed in range(12):
        params = make_params(
            building_type=BuildingType.HOTEL, width=56, depth=36, wealth=Wealth.HIGH, seed=seed
        )
        ground = generate(params).floor(0)
        rooms = {r.type: r for r in ground.rooms}
        kitchen, restaurant = rooms.get("commercial_kitchen"), rooms.get("restaurant")
        if kitchen is None or restaurant is None:
            continue
        for door in ground.openings:
            if door.kind is OpeningKind.DOOR:
                a, b = door.edges[0].cells()
                if {a in kitchen.cells, b in kitchen.cells} == {True, False} and (
                    a in restaurant.cells or b in restaurant.cells
                ):
                    linked += 1
    assert linked >= 6


def test_bigger_office_floors_get_more_toilets() -> None:
    def toilets(width: int, depth: int) -> int:
        building = generate(make_params(width=width, depth=depth, floors_above=2))
        return sum(r.type in ("toilet", "wc") for r in building.floor(1).rooms)

    assert toilets(80, 56) > toilets(40, 24) >= 2


def test_public_toilets_have_stalls_private_bathrooms_not() -> None:
    for building_type in (BuildingType.OFFICE, BuildingType.APARTMENT):
        building = generate(make_params(building_type=building_type, width=60, depth=40, seed=3))
        for floor in building.floors:
            rooms = {r.id: r for r in floor.rooms}
            for obj in floor.objects:
                if obj.kind == "wc":
                    assert rooms[obj.room].type in ("stall", "wc", "bathroom", "bedsit")
            stalls = [r for r in floor.rooms if r.type == "stall"]
            if building_type is BuildingType.APARTMENT:
                assert not stalls
            for stall in stalls:
                # Entered only from its toilet, through one door.
                doors = [
                    o
                    for o in floor.openings
                    if o.kind is OpeningKind.DOOR
                    and any(c in stall.cells for e in o.edges for c in e.cells())
                ]
                assert len(doors) == 1
                outside = next(c for c in doors[0].edges[0].cells() if c not in stall.cells)
                assert next(r for r in floor.rooms if outside in r.cells).type == "toilet"
    office = generate(make_params(width=60, depth=40, seed=3))
    assert any(r.type == "stall" for f in office.floors for r in f.rooms)


def test_row_leftovers_dont_inflate_rooms() -> None:
    """Leftover width goes to rooms with room to grow, a storeroom or a hallway."""
    cases = [
        (BuildingType.COFFIN_BLOCK, 40, 63, 379),
        (BuildingType.COSMETIC_CLINIC, 79, 73, 383),
        (BuildingType.OFFICE, 71, 64, 961),
    ]
    for building_type, width, depth, seed in cases:
        params = make_params(building_type=building_type, width=width, depth=depth, seed=seed)
        building = generate(params)
        rules = rules_for(building_type, params.wealth)
        for floor in building.floors:
            for room in floor.rooms:
                if room.type in ("coffin_unit", "consultation_room", "office"):
                    assert len(room.cells) <= rules.spec(room.type).area[1] * 1.5, room.type


@pytest.mark.parametrize(
    ("building_type", "width", "depth", "wealth", "seed"),
    [
        (BuildingType.APARTMENT, 72, 66, Wealth.SQUATTER, 198),
        (BuildingType.APARTMENT, 76, 57, Wealth.SQUATTER, 215),
        (BuildingType.APARTMENT, 53, 41, Wealth.HIGH, 123),
        (BuildingType.HOTEL, 58, 41, Wealth.LUXURY, 270),
        (BuildingType.CLINIC, 44, 55, Wealth.SQUATTER, 323),
        (BuildingType.CORP_OFFICE, 37, 47, Wealth.SQUATTER, 261),
    ],
)
def test_units_and_storerooms_keep_near_their_maximum(
    building_type: BuildingType, width: int, depth: int, wealth: Wealth, seed: int
) -> None:
    """Spare width goes to closets and more storerooms, not to one room."""
    params = make_params(
        building_type=building_type,
        width=width,
        depth=depth,
        wealth=wealth,
        seed=seed,
        floors_above=2,
        floors_below=1,
    )
    building = generate(params)
    rules = rules_for(building_type, wealth)
    for floor in building.floors:
        for room in floor.rooms:
            if room.unit is not None or room.type == "storage":
                assert len(room.cells) <= rules.spec(room.type).area[1] * 1.5, room


def test_police_ground_floor_has_records_not_storerooms() -> None:
    for seed in range(4):
        params = make_params(
            building_type=BuildingType.POLICE_STATION, width=48, depth=40, seed=seed
        )
        types = [r.type for r in generate(params).floor(0).rooms]
        assert "storage" not in types
        assert "booking_room" in types


@pytest.mark.parametrize(
    "building_type", [BuildingType.OFFICE, BuildingType.HOTEL, BuildingType.HOSPITAL]
)
def test_stairs_and_core_doors_line_up_on_every_floor(building_type: BuildingType) -> None:
    params = make_params(
        building_type=building_type, width=60, depth=44, floors_above=2, floors_below=1, seed=5
    )
    building = generate(params)
    stairs = {
        tuple(sorted((o.x, o.y, o.w, o.h) for o in f.objects if o.kind == "stairs"))
        for f in building.floors
    }
    assert len(stairs) == 1
    doors: set[tuple[Edge, ...]] = set()
    for floor in building.floors:
        stairwell = next(r for r in floor.rooms if r.type == "stairwell")
        doors |= {
            o.edges
            for o in floor.openings
            if o.kind is OpeningKind.DOOR
            and any(c in stairwell.cells for e in o.edges for c in e.cells())
            and o.entrance is None
        }
    assert len(doors) == 1


@pytest.mark.parametrize(
    ("building_type", "width", "depth", "shape", "street_side", "seed"),
    [
        (BuildingType.CLINIC, 58, 30, Shape.U, Side.N, 1393621959),
        (BuildingType.CORP_LAB, 40, 48, Shape.U, Side.E, 4),
    ],
)
def test_a_walled_in_storeroom_opens_onto_the_stairs_not_into_the_elevator(
    building_type: BuildingType, width: int, depth: int, shape: Shape, street_side: Side, seed: int
) -> None:
    """Between stairwell, elevator, toilet stalls and the facade: the landing, not the car."""
    params = make_params(
        building_type=building_type,
        width=width,
        depth=depth,
        floors_above=3 if building_type is BuildingType.CORP_LAB else 2,
        wealth=Wealth.LUXURY if building_type is BuildingType.CORP_LAB else Wealth.MIDDLE,
        shape=shape,
        street_side=street_side,
        seed=seed,
    )
    stranded = 0
    for floor in generate(params).floors:
        for door in floor.openings:
            if door.kind is not OpeningKind.DOOR or door.entrance is not None:
                continue
            sides = {floor.room_at(c) for c in door.edges[0].cells()}
            types = {r.type for r in sides if r is not None}
            if "storage" in types and "stairwell" in types:
                stranded += 1
        for room in floor.rooms:
            if room.type == "elevator":
                doors = [
                    o
                    for o in floor.openings
                    if o.kind is OpeningKind.DOOR
                    and any(c in room.cells for e in o.edges for c in e.cells())
                ]
                assert len(doors) == 1, floor.name
    assert stranded


@pytest.mark.parametrize(
    "building_type", [BuildingType.OFFICE, BuildingType.HOTEL, BuildingType.HOSPITAL]
)
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_big_floors_have_a_second_stairwell_with_its_own_exit(
    building_type: BuildingType, seed: int
) -> None:
    params = make_params(building_type=building_type, width=70, depth=40, floors_above=3, seed=seed)
    building = generate(params)
    for floor in building.floors:
        stairs = [r for r in floor.rooms if r.type == "stairwell"]
        assert len(stairs) == 2, floor.name
        a, b = (min(r.cells) for r in stairs)
        assert abs(a.x - b.x) + abs(a.y - b.y) >= FAR_CORE_MIN
    ground = building.floor(0)
    far = [r for r in ground.rooms if r.type == "stairwell"][1]
    assert any(
        o.entrance == EntranceKind.EMERGENCY
        and any(c in far.cells for e in o.edges for c in e.cells())
        for o in ground.openings
    )
    # Small floors keep one.
    small = generate(make_params(building_type=building_type, width=44, depth=30, floors_above=3))
    assert all(sum(r.type == "stairwell" for r in f.rooms) == 1 for f in small.floors)


@pytest.mark.parametrize(
    "building_type",
    [BuildingType.OFFICE, BuildingType.HOSPITAL, BuildingType.APARTMENT, BuildingType.WAREHOUSE],
)
def test_core_rooms_are_as_big_as_their_stairs_and_car(building_type: BuildingType) -> None:
    """The stairs span the stairwell with a landing at the door; the car is the shaft."""
    for width, depth in ((40, 32), (70, 44)):
        params = make_params(
            building_type=building_type, width=width, depth=depth, floors_above=2, seed=1
        )
        for floor in generate(params).floors:
            for room in floor.rooms:
                if room.type not in ("stairwell", "elevator"):
                    continue
                kind = "stairs" if room.type == "stairwell" else "elevator_car"
                (obj,) = [o for o in floor.objects if o.room == room.id and o.kind == kind]
                if room.type == "elevator":
                    assert obj.cells == room.cells
                    continue
                assert len(room.cells) <= 2 * len(obj.cells), (width, depth, floor.name)
                door = floor.door_clearance(room)
                assert door and not door & obj.cells  # the landing


@pytest.mark.parametrize(
    ("building_type", "width", "depth", "wealth"),
    [
        (BuildingType.STUFFER_SHACK, 24, 16, Wealth.MIDDLE),
        (BuildingType.STUFFER_SHACK, 24, 16, Wealth.LUXURY),
        (BuildingType.DIVE_BAR, 22, 16, Wealth.MIDDLE),
        (BuildingType.CHURCH, 40, 44, Wealth.HIGH),
    ],
)
def test_small_hall_buildings_have_no_service_corridor(
    building_type: BuildingType, width: int, depth: int, wealth: Wealth
) -> None:
    """The back rooms open onto the hall, on every floor; there is still an emergency exit."""
    for seed in range(3):
        params = make_params(
            building_type=building_type,
            width=width,
            depth=depth,
            wealth=wealth,
            floors_above=2 + seed % 2,
            floors_below=seed % 2,
            seed=seed,
        )
        building = generate(params)
        ground = building.floor(0)
        for room in ground.rooms:
            if room.type == "corridor":  # side hallways of clusters only, no service corridor
                xs, ys = {c.x for c in room.cells}, {c.y for c in room.cells}
                assert len(xs) < building.width and len(ys) < building.height
        kinds = {o.entrance for o in ground.openings if o.entrance is not None}
        assert EntranceKind.EMERGENCY.value in kinds, seed
        assert hard_violations(building, rules_for(params.building_type, params.wealth)) == []


@pytest.mark.parametrize(
    ("building_type", "lobby"),
    [
        (BuildingType.CLINIC, "reception_lobby"),
        (BuildingType.HOTEL, "reception_lobby"),
        (BuildingType.POLICE_STATION, "police_lobby"),
    ],
)
def test_the_reception_desk_stands_in_the_lobby(building_type: BuildingType, lobby: str) -> None:
    ground = generate(make_params(building_type=building_type, width=60, depth=40)).floor(0)
    lobbies = {r.id for r in ground.rooms if r.type == lobby}
    assert len(lobbies) == 1 and "reception" not in {r.type for r in ground.rooms}
    desks = [o for o in ground.objects if o.kind == "reception_desk"]
    assert desks and all(o.room in lobbies for o in desks)


def test_the_police_lobby_has_one_door_into_the_station_and_a_visitor_wc() -> None:
    ground = generate(
        make_params(building_type=BuildingType.POLICE_STATION, width=64, depth=44, seed=5)
    ).floor(0)
    lobby = next(r for r in ground.rooms if r.type == "police_lobby")
    inner = [
        o
        for o in ground.openings
        if o.kind is OpeningKind.DOOR
        and o.entrance is None
        and any(c in lobby.cells for e in o.edges for c in e.cells())
    ]
    beyond: list[str] = []
    for door in inner:
        outside = next(c for c in door.edges[0].cells() if c not in lobby.cells)
        room = ground.room_at(outside)
        assert room is not None
        beyond.append(room.type)
    assert sorted(beyond) == ["corridor", "wc"]


def test_waiting_rooms_are_beside_the_reception_lobby() -> None:
    """A room whose `access` names the lobby goes beside it, ahead of other rooms near the
    entrance (toilets): 60 % of clinic and 40 % of hospital waiting rooms did before."""
    rng = random.Random(4)
    beside, total = 0, 0
    for building_type in (BuildingType.CLINIC, BuildingType.HOSPITAL):
        for _ in range(8):
            params = make_params(
                building_type=building_type,
                width=rng.randint(48, 70),
                depth=rng.randint(40, 48),
                seed=rng.randrange(2**32),
            )
            ground = generate(params).floor(0)
            lobby = next(r for r in ground.rooms if r.type == "reception_lobby")
            for room in ground.rooms:
                if room.type == "waiting_room":
                    total += 1
                    beside += any(
                        c.neighbour(side) in lobby.cells for c in room.cells for side in Side
                    )
    assert beside >= 0.85 * total, f"{beside} of {total}"


def test_the_waiting_room_opens_onto_the_reception_lobby() -> None:
    ground = generate(
        make_params(building_type=BuildingType.CLINIC, width=60, depth=32, floors_above=2, seed=1)
    ).floor(0)
    waiting = next(r for r in ground.rooms if r.type == "waiting_room")
    door = next(
        o
        for o in ground.openings
        if o.kind is OpeningKind.DOOR and any(c in waiting.cells for c in o.edges[0].cells())
    )
    outside = next(c for c in door.edges[0].cells() if c not in waiting.cells)
    room = ground.room_at(outside)
    assert room is not None and room.type == "reception_lobby"


def test_the_club_checks_coats_at_the_door() -> None:
    ground = generate(make_params(building_type=BuildingType.NIGHTCLUB, width=40, depth=32)).floor(
        0
    )
    hall = next(r for r in ground.rooms if r.type == "dance_floor")
    kinds = {o.kind for o in ground.objects if o.room == hall.id}
    assert {"coat_rack", "counter"} <= kinds
    assert "cloakroom" not in {r.type for r in ground.rooms}


def _door_between(floor: Floor, a: str, b: str) -> bool:
    for opening in floor.openings:
        if opening.kind is OpeningKind.DOOR:
            rooms = [floor.room_at(c) for c in opening.edges[0].cells()]
            if {r.type for r in rooms if r is not None} == {a, b}:
                return True
    return False


def test_loading_bays_open_onto_the_warehouse_floor() -> None:
    for seed in range(3):
        ground = generate(
            make_params(building_type=BuildingType.WAREHOUSE, width=60, depth=44, seed=seed)
        ).floor(0)
        assert _door_between(ground, "loading_bay", "warehouse_floor"), seed


@pytest.mark.parametrize(
    "building_type",
    [
        BuildingType.WAREHOUSE,
        BuildingType.SUPERMARKET,
        BuildingType.POLICE_STATION,
        BuildingType.HOSPITAL,
        BuildingType.CHOP_SHOP,
    ],
)
def test_vehicle_bays_have_their_own_exterior_door(building_type: BuildingType) -> None:
    for seed in range(3):
        params = make_params(building_type=building_type, width=60, depth=44, seed=seed)
        ground = generate(params).floor(0)
        rules = rules_for(building_type, params.wealth)
        bays = [r for r in ground.rooms if rules.spec(r.type).facade_door]
        assert bays, seed
        for bay in bays:
            width = rules.spec(bay.type).facade_door
            assert width is not None
            assert any(
                o.entrance is not None
                and (o.entrance == EntranceKind.SERVICE.value or len(o.edges) >= width)
                and all(c in bay.cells or c not in ground.footprint for c in o.edges[0].cells())
                for o in ground.openings
            ), (seed, bay.type)


def test_observation_rooms_sit_beside_an_interview_room() -> None:
    beside = 0
    for seed in range(4):
        ground = generate(
            make_params(building_type=BuildingType.POLICE_STATION, width=64, depth=44, seed=seed)
        ).floor(0)
        beside += _door_between(ground, "interview_room", "observation_room")
    assert beside >= 2


def _touch(a: Room, b: Room) -> bool:
    return any(c.neighbour(side) in b.cells for c in a.cells for side in Side)


@pytest.mark.parametrize(
    ("building_type", "room", "partner", "share"),
    [
        # 50 % before: bigger rooms took the interview room's segment first.
        (BuildingType.POLICE_STATION, "observation_room", "interview_room", 0.75),
        # 5 % before: the windowless room kept off the OR's facade row.
        (BuildingType.COSMETIC_CLINIC, "sterilization", "operating_room", 0.75),
    ],
)
def test_next_to_rooms_sit_beside_their_partner(
    building_type: BuildingType, room: str, partner: str, share: float
) -> None:
    rng = random.Random(3)
    beside, total = 0, 0
    for _ in range(10):
        params = make_params(
            building_type=building_type,
            width=rng.randint(40, 70),
            depth=rng.randint(40, 56),
            floors_above=2,
            seed=rng.randrange(2**32),
        )
        for floor in generate(params).floors:
            partners = [r for r in floor.rooms if r.type == partner]
            for r in floor.rooms:
                if r.type == room:
                    assert partners, f"{room} without {partner} on {floor.name}"
                    total += 1
                    beside += any(_touch(r, p) for p in partners)
    assert total and beside >= share * total, f"{beside} of {total}"


def test_the_supermarket_ground_floor_has_a_stockroom_on_the_sales_floor() -> None:
    rng = random.Random(5)
    for _ in range(8):
        params = make_params(
            building_type=BuildingType.SUPERMARKET,
            width=rng.randint(44, 64),
            depth=rng.randint(40, 50),
            seed=rng.randrange(2**32),
        )
        ground = generate(params).floor(0)
        hall = next(r for r in ground.rooms if r.type == "sales_floor")
        stock = [r for r in ground.rooms if r.type == "stockroom"]
        assert any(_touch(s, hall) for s in stock), params.seed


def test_police_station_has_holding_cells_behind_the_lockup() -> None:
    building = generate(
        make_params(building_type=BuildingType.POLICE_STATION, width=64, depth=44, seed=5)
    )
    ground = building.floor(0)
    cells = [r for r in ground.rooms if r.type == "holding_cell"]
    assert len(cells) >= 2
    types = {r.id: r.type for r in ground.rooms}
    for cell in cells:
        doors = [
            o
            for o in ground.openings
            if o.kind is OpeningKind.DOOR
            and any(c in cell.cells for e in o.edges for c in e.cells())
        ]
        assert len(doors) == 1
        outside = next(c for c in doors[0].edges[0].cells() if c not in cell.cells)
        assert types[next(r.id for r in ground.rooms if outside in r.cells)] == "lockup"


def test_parking_decks_have_cars_and_ramps_that_line_up() -> None:
    params = make_params(
        building_type=BuildingType.PARKING_GARAGE, width=60, depth=40, floors_above=3, seed=1
    )
    building = generate(params)
    ramps = {
        tuple((o.x, o.y, o.w, o.h) for o in f.objects if o.kind == "ramp") for f in building.floors
    }
    assert len(ramps) == 1 and next(iter(ramps))
    for floor in building.floors:
        assert sum(o.kind == "car" for o in floor.objects) >= 12


@pytest.mark.parametrize(
    ("building_type", "hall", "objects"),
    [
        (BuildingType.CHURCH, "nave", {"pew", "altar"}),
        (BuildingType.FACTORY, "factory_floor", {"conveyor", "machinery"}),
        (BuildingType.DIVE_BAR, "taproom", {"bar_counter", "stool"}),
        (BuildingType.CHOP_SHOP, "workshop_floor", {"car_lift", "workbench"}),
    ],
)
def test_new_halls_are_furnished(building_type: BuildingType, hall: str, objects: set[str]) -> None:
    ground = generate(make_params(building_type=building_type, width=56, depth=40)).floor(0)
    halls = {r.id for r in ground.rooms if r.type == hall}
    assert halls
    assert objects <= {o.kind for o in ground.objects if o.room in halls}


def test_storeroom_behind_the_stairwell_is_reachable() -> None:
    """Walled in by apartments, it opens into the stairwell as a last resort."""
    params = make_params(
        building_type=BuildingType.APARTMENT,
        width=31,
        depth=81,
        floors_above=3,
        wealth=Wealth.SQUATTER,
        street_side=Side.N,
        service_side=Side.N,
        seed=0,
    )
    building = generate(params)
    assert hard_violations(building, rules_for(params.building_type, params.wealth)) == []


@pytest.mark.parametrize(
    "building_type", [BuildingType.STUFFER_SHACK, BuildingType.STREET_DOC, BuildingType.CHOP_SHOP]
)
def test_small_shops_get_a_staff_wc_instead_of_a_public_toilet(
    building_type: BuildingType,
) -> None:
    building = generate(make_params(building_type=building_type, width=40, depth=32, seed=3))
    types = [r.type for f in building.floors for r in f.rooms]
    assert "wc" in types
    assert "toilet" not in types and "stall" not in types


def test_stall_wc_backs_onto_the_wall_opposite_the_door() -> None:
    building = generate(make_params(width=48, depth=32, seed=3))
    faced = 0
    for floor in building.floors:
        clearances = floor.door_clearances()
        for obj in floor.objects:
            room = next(r for r in floor.rooms if r.id == obj.room)
            if obj.kind != "wc" or room.type != "stall":
                continue
            dx, dy = obj.facing.delta
            door = clearances[room.id]
            cx = sum(c.x for c in door) / len(door) - (obj.x + obj.w / 2 - 0.5)
            cy = sum(c.y for c in door) / len(door) - (obj.y + obj.h / 2 - 0.5)
            assert dx * cx + dy * cy > 0, obj
            faced += 1
    assert faced


def test_lone_room_is_the_whole_footprint() -> None:
    building = generate(make_params(room="meeting_room", width=12, depth=10, floors_above=3))
    assert [f.level for f in building.floors] == [0]
    (floor,) = building.floors
    assert [r.type for r in floor.rooms] == ["meeting_room"]
    assert len(floor.rooms[0].cells) == 12 * 10
    main = [o for o in floor.openings if o.entrance == "main"]
    assert len(main) == 1 and all(e.y == 10 for e in main[0].edges)  # on the street side
    assert floor.objects
    assert not hard_violations(building, rules_for("office", Wealth.MIDDLE))


def test_lone_room_carves_stalls_and_subdivides_units() -> None:
    coffins = generate(make_params(building_type="coffin_block", room="coffin_hall", width=20))
    types = {r.type for r in coffins.floors[0].rooms}
    assert {"coffin_aisle", "coffin_unit"} <= types
    flat = generate(make_params(building_type="apartment", room="apartment", width=24, depth=16))
    assert len({r.unit for r in flat.floors[0].rooms}) == 1
    assert len(flat.floors[0].rooms) > 1


def test_unknown_lone_room_lists_the_known_ones() -> None:
    with pytest.raises(InfeasibleError, match=r"no room type 'pool'.*meeting_room"):
        generate(make_params(room="pool"))


@pytest.mark.parametrize(
    ("building_type", "width", "depth", "wealth", "floors", "seed"),
    [
        (BuildingType.OFFICE, 67, 44, Wealth.HIGH, 2, 683244),
        (BuildingType.CLINIC, 55, 34, Wealth.LOW, 1, 918064),
        (BuildingType.CORP_LAB, 46, 31, Wealth.LOW, 1, 264856),
        (BuildingType.COSMETIC_CLINIC, 67, 39, Wealth.LOW, 1, 934576),
        (BuildingType.STREET_DOC, 60, 40, Wealth.MIDDLE, 1, 5),
        (BuildingType.NIGHTCLUB, 43, 49, Wealth.LUXURY, 2, 598980),
        (BuildingType.CHURCH, 62, 42, Wealth.MIDDLE, 1, 517971),
    ],
)
def test_back_door_opens_into_a_back_of_house_room(
    building_type: BuildingType, width: int, depth: int, wealth: Wealth, floors: int, seed: int
) -> None:
    """Not into an office or exam room that happens to be on the service facade."""
    params = make_params(
        building_type=building_type,
        width=width,
        depth=depth,
        wealth=wealth,
        floors_above=floors,
        seed=seed,
    )
    ground = generate(params).floor(0)
    rules = rules_for(building_type, wealth)
    (door,) = [o for o in ground.openings if o.entrance == "service"]
    room = next(r for c in door.edges[0].cells() if (r := ground.room_at(c)) is not None)
    spec = rules.spec(room.type)
    assert spec.facade_door or spec.circulation or room.type in rules.program.service_rooms


def test_elevator_doors_slide_and_others_swing() -> None:
    building = generate(make_params(width=60, depth=40, floors_above=3, seed=1))
    sliding = 0
    for floor in building.floors:
        for door in floor.openings:
            if door.kind is not OpeningKind.DOOR:
                continue
            types = {r.type for c in door.edges[0].cells() if (r := floor.room_at(c))}
            assert door.sliding == ("elevator" in types)
            sliding += door.sliding
    assert sliding >= len(building.floors)
