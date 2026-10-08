"""Partition layout whose corridors follow real buildings (ADR 0017).

`data/osm_corridors.yaml` holds corridor centrelines taken from OpenStreetMap indoor mapping
(`tools/osm_corridors.py`). Instead of straight bands the skeleton is one of those shapes:
rotated or mirrored, scaled to the footprint, thickened to the corridor width. Its axis-parallel
stretches are plain corridors; the 45 degree ones are staircase bands that the partition layout
already bevels into diagonal walls. Branching, lobby, core and the rest are the partition
layout's.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass
from functools import cache
from importlib import resources

import yaml

from roomplanner.geometry import Cell
from roomplanner.pipeline.base import Context
from roomplanner.pipeline.layout.partition import PartitionLayout
from roomplanner.pipeline.layout.regions import bbox, thicken
from roomplanner.pipeline.registry import register

MIN_SCALE = 0.4  # a shape is shrunk or stretched by at most this much...
MAX_SCALE = 4.0  # ...and that much
FILL = 0.4  # the shape's shorter side fills at least this share of the room it is given
SLANTED = 0.4  # score bonus of shapes with diagonal corridors: the point of this layout
TOP = 8  # shapes drawn from the best this many (by fit)

Path = list[tuple[int, int]]


@dataclass(frozen=True)
class Shape:
    ident: str
    diagonal: int  # cells of 45 degree corridor
    paths: tuple[tuple[tuple[int, int], ...], ...]


@cache
def shapes() -> tuple[Shape, ...]:
    text = (resources.files("roomplanner") / "data" / "osm_corridors.yaml").read_text("utf-8")
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    return tuple(
        Shape(
            entry["id"],
            entry["diagonal"],
            tuple(tuple((x, y) for x, y in path) for path in entry["paths"]),
        )
        for entry in yaml.load(text, Loader=loader)
    )


def _transform(path: Path, flip: bool, turns: int) -> Path:
    """One of the eight symmetries of the square: mirrored in x, then turned by quarters."""
    result: Path = []
    for x, y in path:
        if flip:
            x = -x
        for _ in range(turns):
            x, y = -y, x
        result.append((x, y))
    return result


def _fit(
    shape: Shape, flip: bool, turns: int, room: tuple[int, int]
) -> tuple[float, float, list[Path]] | None:
    """(scale, fill, paths at origin) if the transformed shape fits into `room`."""
    paths = [_transform(list(p), flip, turns) for p in shape.paths]
    xs = [x for p in paths for x, _ in p]
    ys = [y for p in paths for _, y in p]
    wide, high = max(xs) - min(xs), max(ys) - min(ys)
    if wide < 1 or high < 1:
        return None
    scale = min(room[0] / wide, room[1] / high)
    if not MIN_SCALE <= scale <= MAX_SCALE:
        return None
    fill = min(wide * scale / room[0], high * scale / room[1])  # the longer side fills the room
    if fill < FILL:
        return None
    ox, oy = min(xs), min(ys)
    return scale, fill, [[(x - ox, y - oy) for x, y in p] for p in paths]


def _octilinear(a: tuple[int, int], b: tuple[int, int]) -> Path:
    """Cells from a to b in at most two steps of one of the eight directions, the longer one
    first."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    diag = min(abs(dx), abs(dy))
    sx, sy = (dx > 0) - (dx < 0), (dy > 0) - (dy < 0)
    straight = max(abs(dx), abs(dy)) - diag
    pieces: list[tuple[int, int, int]] = []
    if diag:
        pieces.append((sx, sy, diag))
    if straight:
        pieces.append((sx if abs(dx) > abs(dy) else 0, sy if abs(dy) > abs(dx) else 0, straight))
    if len(pieces) == 2 and pieces[1][2] > pieces[0][2]:
        pieces.reverse()
    result = [a]
    for ux, uy, n in pieces:
        last = result[-1]
        result.append((last[0] + ux * n, last[1] + uy * n))
    return result


def _cells(a: tuple[int, int], b: tuple[int, int]) -> list[Cell]:
    """Every cell on the straight (axis-parallel or 45 degree) run a to b."""
    n = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
    ux, uy = (b[0] > a[0]) - (b[0] < a[0]), (b[1] > a[1]) - (b[1] < a[1])
    return [Cell(a[0] + ux * i, a[1] + uy * i) for i in range(n + 1)]


class OsmMixin:
    """The corridor network of a real building, as (straight cells, diagonal cells)."""

    def _network(
        self, ctx: Context, footprint: frozenset[Cell]
    ) -> tuple[frozenset[Cell], frozenset[Cell]]:
        program = ctx.rules.program
        width = program.corridor.width
        rng = ctx.rng("osm")
        x0, y0, x1, y1 = bbox(footprint)
        margin = program.strip_depth[0] + width // 2
        room = (x1 - x0 - 2 * margin, y1 - y0 - 2 * margin)
        if min(room) < 4:
            return frozenset(), frozenset()
        options: list[tuple[float, float, Shape, list[Path]]] = []
        for shape in shapes():
            for flip in (False, True):
                for turns in range(4):
                    found = _fit(shape, flip, turns, room)
                    if found is not None:
                        scale, fill, paths = found
                        bonus = SLANTED if shape.diagonal else 0.0
                        options.append((fill + bonus + rng.uniform(0, 0.15), scale, shape, paths))
        if not options:
            return frozenset(), frozenset()
        options.sort(key=lambda o: -o[0])
        _, scale, _, paths = rng.choice(options[:TOP])
        xs = [x for p in paths for x, _ in p]
        ys = [y for p in paths for _, y in p]
        slack = (room[0] - round(max(xs) * scale), room[1] - round(max(ys) * scale))
        at = (
            x0 + margin + rng.randint(0, max(0, slack[0])),
            y0 + margin + rng.randint(0, max(0, slack[1])),
        )
        return _draw(paths, scale, at, width, footprint)

    def _bands(
        self, ctx: Context, footprint: frozenset[Cell], rng: random.Random
    ) -> tuple[frozenset[Cell], list[frozenset[Cell]]]:
        straight, _ = self._network(ctx, footprint)
        return (straight, []) if straight else super()._bands(ctx, footprint, rng)  # type: ignore[misc]

    def _diagonal(
        self,
        ctx: Context,
        footprint: frozenset[Cell],
        corridor: frozenset[Cell],
        rng: random.Random,
    ) -> frozenset[Cell]:
        return self._network(ctx, footprint)[1] - corridor


def _draw(
    paths: list[Path], scale: float, at: tuple[int, int], width: int, footprint: frozenset[Cell]
) -> tuple[frozenset[Cell], frozenset[Cell]]:
    """Thicken the scaled centrelines: axis-parallel runs to a corridor's width, 45 degree runs
    to staircase bands as wide as `_diagonal` makes them."""
    straight: set[Cell] = set()
    slanted: set[Cell] = set()
    wide = width + 2  # cells per row: 45 degrees makes a band 1.4 times thinner
    for path in paths:
        points = [(at[0] + round(x * scale), at[1] + round(y * scale)) for x, y in path]
        for a, b in itertools.pairwise(points):
            for start, end in zip(_octilinear(a, b), _octilinear(a, b)[1:], strict=False):
                run = _cells(start, end)
                if start[0] != end[0] and start[1] != end[1]:
                    slanted.update(
                        Cell(c.x + k, c.y) for c in run for k in range(-wide // 2, wide // 2)
                    )
                else:
                    straight.update(thicken(run, width, footprint))
    return frozenset(straight & footprint), frozenset(slanted & footprint) - frozenset(straight)


@register("layout", "partition_osm")
class OsmPartitionLayout(OsmMixin, PartitionLayout):
    """The partition layout with the corridors of a real building (OpenStreetMap)."""
