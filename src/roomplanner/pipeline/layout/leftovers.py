"""Leftover storerooms join a neighbour instead of standing as rooms of their own.

Allocation turns space that no room fits into storerooms (the program's cluster filler):
slivers beside the core or a corridor stub, the rest of a row whose rooms are all full. After
allocation each such room merges into a neighbour along one whole side if the union stays a
rectangle and the neighbour within its size limit, else a narrow sliver widens the corridor
it borders. Core, unit, annex and stall rooms keep their shape.
"""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache

from roomplanner.geometry import Cell
from roomplanner.pipeline.base import PlannedRoom
from roomplanner.pipeline.layout.allocation import ABSORB_TOLERANCE
from roomplanner.pipeline.layout.regions import contact
from roomplanner.rules import Rules

MAX_ALCOVE = 8  # cells: slivers up to this wide may widen a corridor


def absorb_leftovers(
    rooms: list[PlannedRoom], rules: Rules, keep_out: frozenset[Cell] = frozenset()
) -> list[PlannedRoom]:
    """`keep_out`: cells of a diagonal corridor, which no leftover beside it may widen."""
    fixed = {e.room for e in rules.program.core}
    result = list(rooms)
    for room in rooms:
        if not room.leftover or room not in result or _box(room.cells) is None:
            continue
        hosts = {id(r.host) for r in result if r.host is not None}
        options: list[tuple[int, float, PlannedRoom]] = []
        for other in result:
            if other is room or other.unit is not None or other.host is not None:
                continue
            spec = rules.spec(other.type)
            if other.type in fixed or id(other) in hosts or spec.stalls is not None:
                continue
            if not _joins(room.cells, other.cells):
                continue
            area = len(room.cells) + len(other.cells)
            if spec.circulation:
                if _thickness(room.cells) <= MAX_ALCOVE and not contact(room.cells, keep_out):
                    options.append((1, 0.0, other))
            elif area <= spec.area[1] * ABSORB_TOLERANCE:
                options.append((0, area / spec.area[1], other))
        if not options:
            _split_between_neighbours(room, result, fixed, hosts, rules)
            continue
        _, _, other = min(options, key=lambda o: (o[0], o[1]))
        merged = replace(other, cells=other.cells | room.cells)
        result[result.index(other)] = merged
        result.remove(room)
    return result


def _split_between_neighbours(
    room: PlannedRoom,
    result: list[PlannedRoom],
    fixed: set[str],
    hosts: set[int],
    rules: Rules,
) -> None:
    """Cut a leftover in two and give each half to a different neighbour that can take it."""
    box = _box(room.cells)
    assert box is not None
    x0, y0, x1, y1 = box

    def cut(vertical: bool, at: int) -> tuple[frozenset[Cell], frozenset[Cell]]:
        low = frozenset(c for c in room.cells if (c.x if vertical else c.y) < at)
        return low, room.cells - low

    best: tuple[float, PlannedRoom, PlannedRoom, frozenset[Cell], frozenset[Cell]] | None = None
    for vertical, lo, hi in ((True, x0, x1), (False, y0, y1)):
        for at in range(lo + 1, hi):
            first, second = cut(vertical, at)
            picks = [
                _best_host(half, room, result, fixed, hosts, rules) for half in (first, second)
            ]
            (a, ra), (b, rb) = picks
            if a is None or b is None or a is b:
                continue
            if best is None or max(ra, rb) < best[0]:
                best = (max(ra, rb), a, b, first, second)
    if best is None:
        return
    _, a, b, first, second = best
    result[result.index(a)] = replace(a, cells=a.cells | first)
    result[result.index(b)] = replace(b, cells=b.cells | second)
    result.remove(room)


def _best_host(
    piece: frozenset[Cell],
    room: PlannedRoom,
    result: list[PlannedRoom],
    fixed: set[str],
    hosts: set[int],
    rules: Rules,
) -> tuple[PlannedRoom | None, float]:
    """The neighbour of a rectangle that stays within its size limit, and how full it gets."""
    best: tuple[PlannedRoom | None, float] = (None, 0.0)
    for other in result:
        if other is room or other.unit is not None or other.host is not None:
            continue
        spec = rules.spec(other.type)
        if other.type in fixed or id(other) in hosts or spec.stalls is not None or spec.circulation:
            continue
        if not _joins(piece, other.cells):
            continue
        area = len(piece) + len(other.cells)
        if area <= spec.area[1] * ABSORB_TOLERANCE and (
            best[0] is None or area / spec.area[1] < best[1]
        ):
            best = (other, area / spec.area[1])
    return best


@lru_cache(maxsize=4096)  # the same few rooms are boxed again for every candidate cut
def _box(cells: frozenset[Cell]) -> tuple[int, int, int, int] | None:
    """(x0, y0, x1, y1) if the cells form a rectangle."""
    xs, ys = [c.x for c in cells], [c.y for c in cells]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs) + 1, max(ys) + 1
    return (x0, y0, x1, y1) if len(cells) == (x1 - x0) * (y1 - y0) else None


def _joins(a: frozenset[Cell], b: frozenset[Cell]) -> bool:
    """Both rectangles, touching along one whole side: their union is a rectangle."""
    box_a, box_b = _box(a), _box(b)
    if box_a is None or box_b is None:
        return False
    ax0, ay0, ax1, ay1 = box_a
    bx0, by0, bx1, by1 = box_b
    same_rows = ay0 == by0 and ay1 == by1 and (ax1 == bx0 or bx1 == ax0)
    same_columns = ax0 == bx0 and ax1 == bx1 and (ay1 == by0 or by1 == ay0)
    return same_rows or same_columns


def _thickness(cells: frozenset[Cell]) -> int:
    box = _box(cells)
    assert box is not None
    x0, y0, x1, y1 = box
    return min(x1 - x0, y1 - y0)
