import pytest

from roomplanner.errors import InfeasibleError
from roomplanner.generator import generate
from roomplanner.geometry import Cell, Edge, Side
from roomplanner.model import OpeningKind
from roomplanner.params import BuildingType, EntranceKind, Shape, Wealth
from roomplanner.rules import rules_for
from roomplanner.validation import hard_violations, validate

from .conftest import make_params


def test_same_seed_same_building() -> None:
    assert generate(make_params(seed=3)) == generate(make_params(seed=3))


def test_missing_seed_is_resolved_and_recorded() -> None:
    building = generate(make_params(seed=None))
    assert building.params.seed == building.seed
    assert generate(building.params) == building


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
        building = generate(make_params(building_type=building_type, width=60, depth=40))
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
    building = generate(make_params(shape=Shape.L, width=60, depth=48))
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
    params = make_params(
        building_type=BuildingType.HOSPITAL, width=70, depth=44, floors_above=3, seed=3
    )
    building = generate(params)
    rules = rules_for(params.building_type, params.wealth)
    for floor in building.floors:
        for room in floor.rooms:
            if room.type in ("exam_room", "ward", "doctor_office"):
                assert len(room.cells) <= rules.spec(room.type).area[1] * 1.5, room


def test_kitchen_opens_straight_into_the_restaurant() -> None:
    linked = 0
    for seed in range(4):
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
    assert linked >= 3


def test_bigger_office_floors_get_more_toilets() -> None:
    def toilets(width: int, depth: int) -> int:
        building = generate(make_params(width=width, depth=depth, floors_above=2))
        return sum(r.type == "toilet" for r in building.floor(1).rooms)

    assert toilets(80, 56) > toilets(40, 24) >= 2


def test_public_toilets_have_stalls_private_bathrooms_not() -> None:
    for building_type in (BuildingType.OFFICE, BuildingType.APARTMENT):
        building = generate(make_params(building_type=building_type, width=60, depth=40, seed=3))
        for floor in building.floors:
            rooms = {r.id: r for r in floor.rooms}
            for obj in floor.objects:
                if obj.kind == "wc":
                    assert rooms[obj.room].type in ("stall", "wc", "bathroom")
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
    "building_type", [BuildingType.OFFICE, BuildingType.HOTEL, BuildingType.HOSPITAL]
)
def test_stairs_and_core_doors_line_up_on_every_floor(building_type: BuildingType) -> None:
    params = make_params(
        building_type=building_type, width=60, depth=44, floors_above=3, floors_below=1, seed=5
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
