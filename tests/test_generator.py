import pytest

from roomplanner.errors import InfeasibleError, NotSupportedError
from roomplanner.generator import generate
from roomplanner.geometry import Side
from roomplanner.model import OpeningKind
from roomplanner.params import BuildingType, Shape, Wealth
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
    assert sorted(d.swing.towards for d in exits if d.swing) == [Side.E, Side.N]


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


@pytest.mark.parametrize("shape", [Shape.IRREGULAR])
def test_unimplemented_shapes_are_rejected(shape: Shape) -> None:
    with pytest.raises(NotSupportedError):
        generate(make_params(shape=shape))


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
