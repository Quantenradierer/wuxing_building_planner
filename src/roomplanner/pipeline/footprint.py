"""Footprint strategies: the set of cells a building covers, identical on every floor.

Shapes other than the rectangle are the bounding box minus rectangular cut-outs. Every arm
stays as deep as the building's layout needs for its main part.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from typing import cast

from roomplanner.errors import InfeasibleError
from roomplanner.geometry import Cell, Corner, Diagonal, Side, connected, rectangle
from roomplanner.pipeline.base import Context, FootprintStrategy, LayoutStrategy
from roomplanner.pipeline.registry import register, resolve


@register("footprint", "rectangle")
class RectangleFootprint:
    def footprint(self, ctx: Context) -> frozenset[Cell]:
        return rectangle(0, 0, ctx.width, ctx.height)


def arm_depth(ctx: Context) -> int:
    layout = cast(LayoutStrategy, resolve("layout", ctx.rules.program.layout))
    return layout.main_min_depth(ctx)


@register("footprint", "l")
class LFootprint:
    """The bounding box minus one corner of about 1/3 to 1/2 of each side."""

    def footprint(self, ctx: Context) -> frozenset[Cell]:
        arm = arm_depth(ctx)
        cuts: list[tuple[int, int]] = []
        for side in (ctx.width, ctx.height):
            low, high = max(side // 3, arm // 2), min(side // 2, side - arm)
            if low > high:
                raise InfeasibleError(
                    f"{ctx.width}x{ctx.height} is too small for an L shape, both arms need "
                    f"at least {arm} cells (try {2 * arm}x{2 * arm} or more)"
                )
            cuts.append((low, high))
        rng = ctx.rng("footprint")
        cut_w, cut_h = rng.randint(*cuts[0]), rng.randint(*cuts[1])
        x = 0 if rng.random() < 0.5 else ctx.width - cut_w
        y = 0 if rng.random() < 0.5 else ctx.height - cut_h
        return rectangle(0, 0, ctx.width, ctx.height) - rectangle(x, y, cut_w, cut_h)


@register("footprint", "u")
class UFootprint:
    """The bounding box minus a courtyard notch in the middle of one side.

    The notch opens on the longer side if both are possible; the two arms and the base keep
    the layout's minimum depth.
    """

    def footprint(self, ctx: Context) -> frozenset[Cell]:
        arm = arm_depth(ctx)
        rng = ctx.rng("footprint")
        # (notch opens north/south, notch width range, notch depth range)
        options: list[tuple[bool, tuple[int, int], tuple[int, int]]] = []
        for horizontal, across, along in (
            (True, ctx.width, ctx.height),
            (False, ctx.height, ctx.width),
        ):
            widths = (max(MIN_COURT, across // 4), across - 2 * arm)
            depths = (max(MIN_COURT, along // 3), along - arm)
            if widths[0] <= widths[1] and depths[0] <= depths[1]:
                options.append((horizontal, widths, depths))
        if not options:
            raise InfeasibleError(
                f"{ctx.width}x{ctx.height} is too small for a U shape, the arms and the base "
                f"need at least {arm} cells each (try {3 * arm}x{2 * arm} or more)"
            )
        horizontal, widths, depths = rng.choice(options)
        across, along = (ctx.width, ctx.height) if horizontal else (ctx.height, ctx.width)
        notch_w, notch_d = rng.randint(*widths), rng.randint(*depths)
        left = rng.randint(arm, across - arm - notch_w)
        start = 0 if rng.random() < 0.5 else along - notch_d
        if horizontal:
            notch = rectangle(left, start, notch_w, notch_d)
        else:
            notch = rectangle(start, left, notch_d, notch_w)
        return rectangle(0, 0, ctx.width, ctx.height) - notch


MIN_COURT = 4  # cells; narrowest courtyard or notch

type Cut = tuple[int, int, int, int]  # x, y, width, height
type Planner = Callable[[int, int, int, random.Random], list[Cut] | None]


def _t_cuts(w: int, h: int, arm: int, rng: random.Random) -> list[Cut] | None:
    """A bar along the top, a stem down from its middle."""
    bar = (max(arm, h // 3), h - max(MIN_COURT, h // 3))
    stem = (max(arm, w // 4), w - 2 * MIN_COURT)
    if bar[0] > bar[1] or stem[0] > stem[1]:
        return None
    b, s = rng.randint(*bar), rng.randint(*stem)
    slack = (w - s) // 2
    left = min(w - s - MIN_COURT, max(MIN_COURT, slack + rng.randint(-w // 8, w // 8)))
    return [(0, b, left, h - b), (left + s, b, w - left - s, h - b)]


def _z_cuts(w: int, h: int, arm: int, rng: random.Random) -> list[Cut] | None:
    """Opposite corners cut: top-right and bottom-left, a full-width band between them."""
    depth = (max(MIN_COURT, h // 5), (h - arm) // 2)
    width = (max(MIN_COURT, w // 4), w - arm)
    if depth[0] > depth[1] or width[0] > width[1]:
        return None
    ha, hb = rng.randint(*depth), rng.randint(*depth)
    ca, cb = rng.randint(*width), rng.randint(*width)
    return [(w - ca, 0, ca, ha), (0, h - hb, cb, hb)]


def _stepped_cuts(w: int, h: int, arm: int, rng: random.Random) -> list[Cut] | None:
    """Two steps down from the top-right corner, every step at least one arm wide and deep."""
    if w < 3 * arm or h < 2 * arm + MIN_COURT:
        return None
    x1 = rng.randint(arm, w - 2 * arm)
    x2 = rng.randint(x1 + arm, w - arm)
    y1 = rng.randint(MIN_COURT, h - 2 * arm)
    y2 = rng.randint(y1 + arm, h - arm)
    return [(x1, 0, w - x1, y1), (x2, y1, w - x2, y2 - y1)]


def _oriented(ctx: Context, planner: Planner, name: str, rng: random.Random) -> frozenset[Cell]:
    """Plan the cuts in either orientation, then rotate / mirror the result at random."""
    arm = arm_depth(ctx)
    plans: list[tuple[bool, list[Cut]]] = []
    for transpose in (False, True):
        w, h = (ctx.height, ctx.width) if transpose else (ctx.width, ctx.height)
        if (cuts := planner(w, h, arm, rng)) is not None:
            plans.append((transpose, cuts))
    if not plans:
        raise InfeasibleError(
            f"{ctx.width}x{ctx.height} is too small for {name} shape, every arm needs at "
            f"least {arm} cells"
        )
    transpose, cuts = rng.choice(plans)
    flip_x, flip_y = rng.random() < 0.5, rng.random() < 0.5
    removed = frozenset[Cell]().union(*(rectangle(*cut) for cut in cuts))
    cells: set[Cell] = set()
    w, h = (ctx.height, ctx.width) if transpose else (ctx.width, ctx.height)
    for cell in rectangle(0, 0, w, h) - removed:
        x, y = (cell.y, cell.x) if transpose else (cell.x, cell.y)
        x = ctx.width - 1 - x if flip_x else x
        y = ctx.height - 1 - y if flip_y else y
        cells.add(Cell(x, y))
    return frozenset(cells)


@register("footprint", "t")
class TFootprint:
    def footprint(self, ctx: Context) -> frozenset[Cell]:
        return _oriented(ctx, _t_cuts, "a T", ctx.rng("footprint"))


@register("footprint", "z")
class ZFootprint:
    def footprint(self, ctx: Context) -> frozenset[Cell]:
        return _oriented(ctx, _z_cuts, "a Z", ctx.rng("footprint"))


@register("footprint", "stepped")
class SteppedFootprint:
    def footprint(self, ctx: Context) -> frozenset[Cell]:
        return _oriented(ctx, _stepped_cuts, "a stepped", ctx.rng("footprint"))


@register("footprint", "irregular")
class IrregularFootprint:
    """One of the other non-rectangular shapes, picked by the seed among those that fit."""

    SHAPES = ("l", "u", "t", "z", "stepped")

    def footprint(self, ctx: Context) -> frozenset[Cell]:
        order = list(self.SHAPES)
        ctx.rng("footprint:irregular").shuffle(order)
        for name in order:
            try:
                return cast(FootprintStrategy, resolve("footprint", name)).footprint(ctx)
            except InfeasibleError:
                continue
        raise InfeasibleError(
            f"{ctx.width}x{ctx.height} is too small for any irregular shape, "
            f"every arm needs at least {arm_depth(ctx)} cells"
        )


MIN_CHAMFER = 2  # cells; a shorter cut is no diagonal
CHAMFER_MARGIN = 2  # cells of straight facade kept between two cuts and at their ends


def chamfer(
    footprint: frozenset[Cell], size: int, rooms: Iterable[frozenset[Cell]]
) -> tuple[frozenset[Cell], frozenset[Diagonal]]:
    """Cut the convex corners of the footprint with 45 degree walls `size` cells long.

    Returns the cells that leave the footprint (wholly outside the diagonal) and the
    diagonals across the cells it crosses, which stay as half floor. A corner is cut where
    both its facades are straight for `2 * size + CHAMFER_MARGIN` cells (so no two cuts
    meet), the square behind it is floor and no room (of any floor) would lose a quarter of
    its cells or fall apart. A tight corner gets the biggest cut that fits, if it is at least
    `MIN_CHAMFER`.
    """
    if size < MIN_CHAMFER:
        return frozenset(), frozenset()
    rooms = list(rooms)
    removed: set[Cell] = set()
    diagonals: set[Diagonal] = set()
    for cell in sorted(footprint):
        for corner in Corner:
            first, second = corner.sides  # vertical side first (N, S), then horizontal (W, E)
            if cell.neighbour(first) in footprint or cell.neighbour(second) in footprint:
                continue
            along_x = _straight(footprint, cell, second.opposite, first)
            along_y = _straight(footprint, cell, first.opposite, second)
            fit = min(size, (along_x - CHAMFER_MARGIN) // 2, (along_y - CHAMFER_MARGIN) // 2)
            while fit >= MIN_CHAMFER:
                gone, halves = _cut(footprint, cell, corner, fit)
                if gone is not None and halves is not None and _harmless(gone, rooms):
                    removed |= gone
                    diagonals |= halves
                    break
                fit -= 1
    return frozenset(removed), frozenset(diagonals)


def _cut(
    footprint: frozenset[Cell], corner_cell: Cell, corner: Corner, n: int
) -> tuple[set[Cell] | None, set[Diagonal] | None]:
    """The cells going and the diagonals of a cut of n cells; None if the corner is not square."""
    dx = 1 if corner.value[1] == "W" else -1
    dy = 1 if corner.value[0] == "N" else -1
    gone: set[Cell] = set()
    halves: set[Diagonal] = set()
    for i in range(n):
        for j in range(n):
            if Cell(corner_cell.x + dx * i, corner_cell.y + dy * j) not in footprint:
                return None, None
    for i in range(n):
        for j in range(n - i):
            here = Cell(corner_cell.x + dx * i, corner_cell.y + dy * j)
            if i + j == n - 1:
                halves.add(Diagonal(here.x, here.y, corner))
            else:
                gone.add(here)
    return gone, halves


def _harmless(gone: set[Cell], rooms: list[frozenset[Cell]]) -> bool:
    for cells in rooms:
        lost = cells & gone
        if lost and (len(cells) - len(lost) < 0.75 * len(cells) or not connected(cells - lost)):
            return False
    return True


def _straight(footprint: frozenset[Cell], start: Cell, step: Side, outside: Side) -> int:
    """How many cells from `start` in direction `step` have floor and no floor on `outside`."""
    count, cell = 0, start
    while cell in footprint and cell.neighbour(outside) not in footprint:
        count += 1
        cell = cell.neighbour(step)
    return count
