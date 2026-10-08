"""A 45 degree corridor between two parallel ones: diagonal walls between rooms and doors in
them (ADR 0016)."""

from functools import cache

from roomplanner.export.common import ExportOptions
from roomplanner.export.uvtt import to_uvtt
from roomplanner.generator import generate
from roomplanner.model import Building, Floor, OpeningKind
from roomplanner.render.ascii import render_floor
from roomplanner.render.image import RenderOptions
from roomplanner.render.image import render_floor as render_image
from roomplanner.render.theme import load_theme
from roomplanner.serialization import from_json, to_json
from roomplanner.validation import hard_violations

from .conftest import make_params

SEEDS = range(1, 5)


@cache
def office(seed: int) -> Building:
    return generate(
        make_params(width=80, depth=50, floors_above=1, layout="partition_diagonal", seed=seed)
    )


def inner(floor: Floor) -> list[tuple[int, int]]:
    return [(d.x, d.y) for d in floor.diagonals if floor.is_inner_diagonal(d)]


def slanted_floors() -> list[tuple[int, Floor]]:
    return [(s, f) for s in SEEDS for f in office(s).floors if inner(f)]


def diagonal_doors() -> list[tuple[int, Floor]]:
    return [(s, f) for s, f in slanted_floors() if any(o.diagonal for o in f.openings)]


def test_the_diagonal_layout_links_its_corridors_diagonally() -> None:
    assert {s for s, f in slanted_floors() if f.level == 0} == set(SEEDS)


def test_the_plain_layout_has_no_diagonal_corridor() -> None:
    for seed in SEEDS:
        building = generate(make_params(width=80, depth=50, floors_above=1, seed=seed))
        assert not any(inner(f) for f in building.floors)


def test_buildings_with_a_diagonal_corridor_are_valid() -> None:
    for seed, _ in slanted_floors():
        assert hard_violations(office(seed)) == []


def test_the_triangle_across_a_diagonal_belongs_to_one_other_room() -> None:
    for _, floor in slanted_floors():
        owner = {c: r.id for r in floor.rooms for c in r.cells}
        for d in floor.diagonals:
            if floor.is_inner_diagonal(d):
                across = {owner[next(c for c in e.cells() if c != d.cell)] for e in d.edges()}
                assert len(across) == 1
                assert owner[d.cell] not in across
                assert set(d.edges()) <= floor.walls  # hidden walls, the diagonal replaces them


def test_rooms_beside_the_corridor_get_doors_in_its_diagonal_walls() -> None:
    doors = diagonal_doors()
    assert doors
    for _, floor in doors:
        for door in floor.openings:
            if door.diagonal:
                assert door.kind is OpeningKind.DOOR
                assert set(door.edges) <= floor.cut_edges
                assert door.swing is not None
                (x0, y0), (x1, y1) = floor.diagonal_door_line(door)
                assert abs(x1 - x0) == abs(y1 - y0) >= 2  # a slanted run of 2+ cells


def test_a_diagonal_door_joins_the_two_rooms_of_its_wall() -> None:
    for _, floor in diagonal_doors():
        owner = {c: r.id for r in floor.rooms for c in r.cells}
        for door in floor.openings:
            if door.diagonal:
                a, b = door.edges[0].cells()
                assert owner[a] != owner[b]


def test_diagonal_doors_survive_json_and_show_in_every_view() -> None:
    seed, floor = diagonal_doors()[0]
    building = office(seed)
    assert from_json(to_json(building)) == building

    art = render_floor(floor, building.width, building.height)
    assert "/" in art or "\\" in art
    assert "D" in art

    exported = to_uvtt(building, floor, load_theme("neon"), ExportOptions())
    slanted = [
        p
        for p in exported["portals"]
        if p["bounds"][0]["x"] != p["bounds"][1]["x"] and p["bounds"][0]["y"] != p["bounds"][1]["y"]
    ]
    assert slanted

    image = render_image(building, floor, load_theme("neon"), RenderOptions(cell_px=8))
    assert image.size[0] > 0


def test_long_buildings_get_more_diagonal_corridors() -> None:
    def runs(width: int) -> int:
        floor = generate(
            make_params(width=width, depth=50, floors_above=1, layout="partition_diagonal", seed=7)
        ).floors[0]
        # one diagonal wall run per corridor: its cells start at the corridor's top row
        inner_cells = {(d.x, d.y) for d in floor.diagonals if floor.is_inner_diagonal(d)}
        return sum(
            1
            for x, y in inner_cells
            if (x - 1, y - 1) not in inner_cells and (x - 1, y + 1) not in inner_cells
        )

    assert runs(200) > runs(80)
