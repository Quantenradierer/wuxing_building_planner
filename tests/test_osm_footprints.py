"""House-shaped footprints from OpenStreetMap outlines (ADR 0017)."""

from roomplanner.generator import generate
from roomplanner.geometry import connected
from roomplanner.pipeline.footprint_osm import outlines
from roomplanner.validation import hard_violations

from .conftest import make_params


def test_the_outlines_are_rectangular_rows_of_cells() -> None:
    assert len(outlines()) >= 20
    for outline in outlines():
        assert len({len(row) for row in outline.rows}) == 1, outline.ident
        assert any("." in row for row in outline.rows), outline.ident  # not a plain rectangle


def test_outlines_cover_many_kinds_of_building() -> None:
    kinds = {outline.category for outline in outlines()}
    assert {"house", "hotel", "hospital", "industrial", "warehouse", "university"} <= kinds


def test_a_building_type_gets_valid_osm_footprints() -> None:
    for seed in range(1, 4):
        building = generate(
            make_params(building_type="warehouse", width=60, depth=40, shape="osm", seed=seed)
        )
        assert hard_violations(building) == []


def test_osm_footprints_are_connected_and_not_rectangles() -> None:
    odd = 0
    for seed in range(1, 6):
        building = generate(make_params(width=60, depth=40, shape="osm", seed=seed))
        footprint = building.floors[0].footprint
        assert connected(footprint)
        assert hard_violations(building) == []
        odd += len(footprint) < 60 * 40
    assert odd >= 4
