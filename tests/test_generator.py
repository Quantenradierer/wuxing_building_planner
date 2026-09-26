import pytest

from roomplanner.errors import InfeasibleError, NotSupportedError
from roomplanner.generator import generate
from roomplanner.geometry import Side
from roomplanner.model import OpeningKind
from roomplanner.params import BuildingType, Shape
from roomplanner.validation import validate

from .conftest import make_params


def test_same_seed_same_building() -> None:
    assert generate(make_params(seed=3)) == generate(make_params(seed=3))


def test_missing_seed_is_resolved_and_recorded() -> None:
    building = generate(make_params(seed=None))
    assert building.params.seed == building.seed
    assert generate(building.params) == building


def test_dimensions_snap_to_grid() -> None:
    building = generate(make_params(width_m=12.2, depth_m=8.3))
    assert (building.width, building.height) == (24, 17)


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


@pytest.mark.parametrize("building_type", [BuildingType.CLINIC, BuildingType.APARTMENT])
def test_unimplemented_building_types_are_rejected(building_type: BuildingType) -> None:
    with pytest.raises(NotSupportedError, match="not implemented yet"):
        generate(make_params(building_type=building_type))


def test_too_small_footprint_fails_fast() -> None:
    with pytest.raises(InfeasibleError, match="at least"):
        generate(make_params(width_m=1.5))


@pytest.mark.parametrize("shape", [Shape.L, Shape.U, Shape.IRREGULAR])
def test_unimplemented_shapes_are_rejected(shape: Shape) -> None:
    with pytest.raises(NotSupportedError):
        generate(make_params(shape=shape))
