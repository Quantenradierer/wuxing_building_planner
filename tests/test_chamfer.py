"""Chamfered corners: 45 degree walls across the cells at the footprint's convex corners."""

import json
import random
import sys

import pytest

from roomplanner.export.common import ExportOptions
from roomplanner.export.uvtt import to_uvtt
from roomplanner.generator import generate
from roomplanner.geometry import Cell, Corner, Diagonal, rectangle
from roomplanner.model import Building
from roomplanner.pipeline.footprint import chamfer, chamfer_size
from roomplanner.render.ascii import render_floor
from roomplanner.render.image import RenderOptions
from roomplanner.render.image import render_floor as render_image
from roomplanner.render.theme import load_theme
from roomplanner.serialization import from_json, to_json
from roomplanner.validation import hard_violations

from .conftest import make_params


def none(rng: random.Random) -> int:
    return 0


@pytest.fixture(autouse=True)
def cut_four(monkeypatch: pytest.MonkeyPatch) -> None:
    def four(rng: random.Random) -> int:
        return 4

    monkeypatch.setattr(sys.modules["roomplanner.pipeline.run"], "chamfer_size", four)


def chamfered(**overrides: object) -> Building:
    return generate(make_params(width=40, depth=26, **overrides))


def test_the_seed_decides_whether_and_how_much_to_cut() -> None:
    sizes = [chamfer_size(random.Random(seed)) for seed in range(300)]
    assert 60 <= sum(s > 0 for s in sizes) <= 140  # about a third
    assert {s for s in sizes if s} <= set(range(4, 11))
    assert chamfer_size(random.Random(5)) == chamfer_size(random.Random(5))


def test_every_corner_of_a_rectangle_is_cut() -> None:
    floor = chamfered().floors[0]
    assert {d.cut for d in floor.diagonals} == set(Corner)
    assert len(floor.diagonals) == 4 * 4
    # Cells wholly beyond the diagonals leave the footprint: 4 corners of 3 + 2 + 1 cells.
    assert len(floor.footprint) == 40 * 26 - 4 * 6


def test_chamfered_building_is_valid_on_every_floor() -> None:
    building = chamfered(floors_above=3, floors_below=1)
    assert all(f.diagonals for f in building.floors if f.role != "roof")
    assert hard_violations(building) == []


def test_nothing_opens_or_stands_in_a_cut_cell() -> None:
    for floor in chamfered(floors_above=2).floors:
        assert not any(set(o.edges) & floor.cut_edges for o in floor.openings)
        assert not any(o.cells & floor.half_cells for o in floor.objects)


def test_cut_cells_stay_in_their_room() -> None:
    floor = chamfered().floors[0]
    assert all(floor.room_at(d.cell) is not None for d in floor.diagonals)


def test_corners_without_room_stay_square() -> None:
    footprint = rectangle(0, 0, 5, 5)  # straight runs of 5 leave less than 2 cells of cut
    assert chamfer(footprint, 4, [footprint]) == (frozenset(), frozenset())
    assert chamfer(rectangle(0, 0, 20, 20), 1, [footprint]) == (frozenset(), frozenset())


def test_a_tight_corner_gets_a_smaller_cut() -> None:
    footprint = rectangle(0, 0, 6, 6)  # room for 2 cells only, not the 4 asked for
    removed, diagonals = chamfer(footprint, 4, [footprint])
    assert removed == {Cell(0, 0), Cell(5, 0), Cell(0, 5), Cell(5, 5)}
    assert len(diagonals) == 8


def test_a_cut_never_costs_a_room_a_quarter_of_its_cells() -> None:
    footprint = rectangle(0, 0, 20, 20)
    corner_room = rectangle(0, 0, 3, 3)
    removed, diagonals = chamfer(footprint, 4, [corner_room, footprint - corner_room])
    assert len(corner_room & removed) * 4 <= len(corner_room)
    assert Diagonal(0, 0, Corner.NW) not in diagonals  # a cut of 3+ would take 3 of 9 cells


def test_ascii_draws_the_diagonals_instead_of_steps() -> None:
    building = chamfered()
    text = render_floor(building.floors[0], building.width, building.height)
    assert text.count("/") + text.count("\\") >= len(building.floors[0].diagonals)
    assert "+-+" not in text.splitlines()[1]


def test_json_round_trip_keeps_diagonals(monkeypatch: pytest.MonkeyPatch) -> None:
    building = chamfered()
    document = json.loads(to_json(building))
    assert document["floors"][0]["diagonals"][0][2] in {c.value for c in Corner}
    assert from_json(to_json(building)) == building
    monkeypatch.setattr(sys.modules["roomplanner.pipeline.run"], "chamfer_size", none)
    plain = json.loads(to_json(generate(make_params())))
    assert "diagonals" not in plain["floors"][0]


def test_vtt_walls_include_the_diagonals() -> None:
    building = chamfered()
    walls = to_uvtt(building, building.floors[0], load_theme("neon"), ExportOptions(cell_px=6))[
        "line_of_sight"
    ]
    slanted = [w for w in walls if w[0]["x"] != w[1]["x"] and w[0]["y"] != w[1]["y"]]
    assert len(slanted) == len(building.floors[0].diagonals)


def test_image_renders_the_outside_of_a_diagonal_as_background() -> None:
    building = chamfered()
    floor = building.floors[0]
    options = RenderOptions(cell_px=12, padding=1)
    image = render_image(building, floor, load_theme("neon"), options)
    # The very corner of the map is outside the building: the background colour.
    cut, padding = image.getpixel((12 + 2, 12 + 2)), image.getpixel((2, 2))
    assert isinstance(cut, tuple) and isinstance(padding, tuple)
    assert max(abs(a - b) for a, b in zip(cut, padding, strict=True)) <= 6


@pytest.mark.parametrize("shape", ["l", "u", "stepped"])
def test_other_shapes_can_be_chamfered(shape: str) -> None:
    building = generate(make_params(width=56, depth=40, shape=shape))
    assert hard_violations(building) == []
