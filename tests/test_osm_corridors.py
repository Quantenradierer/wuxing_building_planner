"""Corridors shaped like real buildings (ADR 0017)."""

from roomplanner.generator import generate
from roomplanner.pipeline.layout.osm import shapes
from roomplanner.validation import hard_violations

from .conftest import make_params

SEEDS = range(1, 6)


def test_the_shapes_are_octilinear_paths() -> None:
    assert len(shapes()) >= 20
    for shape in shapes():
        for path in shape.paths:
            for (ax, ay), (bx, by) in zip(path, path[1:], strict=False):
                dx, dy = abs(bx - ax), abs(by - ay)
                assert dx == 0 or dy == 0 or dx == dy, shape.ident


def test_osm_buildings_are_valid_and_mostly_slanted() -> None:
    slanted = 0
    for seed in SEEDS:
        building = generate(
            make_params(width=80, depth=50, floors_above=1, layout="partition_osm", seed=seed)
        )
        assert hard_violations(building) == []
        slanted += any(floor.diagonals for floor in building.floors)
    assert slanted >= len(SEEDS) - 1


def test_the_same_seed_gives_the_same_building() -> None:
    params = make_params(width=60, depth=40, floors_above=1, layout="partition_osm", seed=7)
    assert generate(params) == generate(params)
