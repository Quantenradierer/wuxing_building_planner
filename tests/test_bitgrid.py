"""The bitmask geometry must agree with the cell-set geometry it replaces."""

import random

from roomplanner.bitgrid import BitGrid
from roomplanner.geometry import Cell, Side, thinnest_extent
from roomplanner.pipeline.layout.regions import bbox, components

W, H = 14, 11


def _random_cells(rng: random.Random) -> frozenset[Cell]:
    density = rng.choice([0.5, 0.7, 0.9])
    return frozenset(
        Cell(x + 3, y - 2) for x in range(W) for y in range(H) if rng.random() < density
    )


def _blind(rest: frozenset[Cell], border: frozenset[Cell]) -> int:
    seen = set(rest & border)
    for side in Side:
        dx, dy = side.delta
        reach: set[Cell] = set()
        for cell in sorted(rest, key=lambda c: c.x * dx + c.y * dy, reverse=True):
            if cell in border or cell.neighbour(side) in reach:
                reach.add(cell)
        seen |= reach
    return len(rest) - len(seen)


def test_bitgrid_matches_cell_sets() -> None:
    rng = random.Random(7)
    grid = BitGrid(3, -2, 3 + W, -2 + H)
    for _ in range(150):
        cells = _random_cells(rng)
        if not cells:
            continue
        mask = grid.mask(cells)
        assert grid.cells(mask) == cells
        x0, y0, x1, y1 = bbox(cells)
        assert grid.bbox(mask) == (x0, y0, x1, y1)
        assert grid.min_cell(mask) == min(cells)
        assert [grid.cells(m) for m in grid.components(mask)] == components(cells)
        assert grid.connected(mask) == (len(components(cells)) == 1)
        for enough in (1, 2, 3, 4):
            assert grid.thick(mask, mask, enough) == (thinnest_extent(cells, cells) >= enough)
        border = frozenset(c for c in cells if rng.random() < 0.15)
        assert grid.blind(mask, grid.mask(border)) == _blind(cells, border)


def test_rect_and_lines() -> None:
    grid = BitGrid(2, 3, 12, 9)
    box = grid.rect(4, 4, 3, 2)
    assert box.bit_count() == 6
    assert grid.cells(box) == frozenset(Cell(x, y) for x in range(4, 7) for y in (4, 5))
    assert [(k, n) for k, _, n in grid.lines(box, vertical=True)] == [(2, 2), (3, 2), (4, 2)]
    assert [(k, n) for k, _, n in grid.lines(box, vertical=False)] == [(1, 3), (2, 3)]
