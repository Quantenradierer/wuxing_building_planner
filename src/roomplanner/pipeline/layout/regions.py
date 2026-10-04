"""Cell-set helpers for the partition layout: regions of the floor that get cut and typed.

A region is any 4-connected set of cells, so the rooms cut from it can be irregular.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Collection, Iterable
from functools import cache
from importlib import resources

import yaml

from roomplanner.geometry import Cell, Side, largest_rectangle, thinnest_extent
from roomplanner.rules import RoomSpec

MIN_ALCOVE = 3  # cells: the least width of a part of a room beyond its main rectangle


@cache
def size_classes() -> tuple[tuple[str, int], ...]:
    """(name, upper area bound) per class, smallest first, then ("huge", infinity)."""
    text = (resources.files("roomplanner") / "data" / "sizes.yaml").read_text(encoding="utf-8")
    bounds: dict[str, int] = yaml.safe_load(text)["classes"]
    return (*bounds.items(), ("huge", 10**9))


def size_class(area: int) -> int:
    """Index of the size class an area falls in (0 = tiny)."""
    for index, (_, bound) in enumerate(size_classes()):
        if area < bound:
            return index
    return len(size_classes()) - 1


def components(cells: Iterable[Cell]) -> list[frozenset[Cell]]:
    """4-connected pieces, largest first (ties by position, so the order is stable)."""
    left = set(cells)
    found: list[frozenset[Cell]] = []
    while left:
        start = min(left)
        piece = {start}
        queue = deque([start])
        left.discard(start)
        while queue:
            cell = queue.popleft()
            for side in Side:
                other = cell.neighbour(side)
                if other in left:
                    left.discard(other)
                    piece.add(other)
                    queue.append(other)
        found.append(frozenset(piece))
    found.sort(key=lambda piece: (-len(piece), min(piece)))
    return found


def bbox(cells: Collection[Cell]) -> tuple[int, int, int, int]:
    xs, ys = [c.x for c in cells], [c.y for c in cells]
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def is_rect(cells: frozenset[Cell]) -> bool:
    x0, y0, x1, y1 = bbox(cells)
    return (x1 - x0) * (y1 - y0) == len(cells)


def contact(cells: Collection[Cell], other: frozenset[Cell]) -> int:
    """Number of `cells` with a side on `other`."""
    return sum(any(c.neighbour(s) in other for s in Side) for c in cells)


def facade(cells: Collection[Cell], footprint: frozenset[Cell]) -> list[tuple[Cell, Side]]:
    """The (cell, side) pairs of `cells` on the outer wall."""
    return [(c, s) for c in cells for s in Side if c.neighbour(s) not in footprint]


def distances(sources: Iterable[Cell], within: frozenset[Cell]) -> dict[Cell, int]:
    """Steps from the nearest source to every cell of `within` (sources count as 0)."""
    dist = {c: 0 for c in sources}
    queue = deque(sorted(dist))
    while queue:
        cell = queue.popleft()
        for side in Side:
            other = cell.neighbour(side)
            if other in within and other not in dist:
                dist[other] = dist[cell] + 1
                queue.append(other)
    return dist


def descend(start: Cell, dist: dict[Cell, int]) -> list[Cell]:
    """The way from `start` down to a source cell, keeping its direction where it can."""
    path = [start]
    heading: Side | None = None
    while dist[path[-1]] > 0:
        here = path[-1]
        options = [s for s in Side if dist.get(here.neighbour(s), 10**9) < dist[here]]
        side = heading if heading in options else options[0]
        heading = side
        path.append(here.neighbour(side))
    return path[:-1]  # without the source cell itself


def thicken(path: Iterable[Cell], width: int, within: frozenset[Cell]) -> frozenset[Cell]:
    """Every cell within a `width` x `width` square centred on a path cell."""
    low, high = (width - 1) // 2, width // 2
    return frozenset(
        cell
        for p in path
        for dx in range(-low, high + 1)
        for dy in range(-low, high + 1)
        if (cell := Cell(p.x + dx, p.y + dy)) in within
    )


def shape_ok(cells: frozenset[Cell], spec: RoomSpec) -> bool:
    """The validator's width rule: thick enough everywhere, or a rectangle with alcoves."""
    if thinnest_extent(cells, cells) >= spec.min_side:
        return True
    x0, y0, x1, y1 = largest_rectangle(cells)
    if min(x1 - x0, y1 - y0) < spec.min_side:
        return False
    rest = frozenset(c for c in cells if not (x0 <= c.x < x1 and y0 <= c.y < y1))
    return not rest or thinnest_extent(rest, cells) >= min(MIN_ALCOVE, spec.min_side)


def aspect_ok(cells: frozenset[Cell], spec: RoomSpec) -> bool:
    if not is_rect(cells):
        return True
    x0, y0, x1, y1 = bbox(cells)
    long, short = max(x1 - x0, y1 - y0), min(x1 - x0, y1 - y0)
    return long <= spec.max_aspect * short * 1.2


def opened(cells: frozenset[Cell], width: int) -> frozenset[Cell]:
    """The cells covered by some `width` x `width` square inside `cells`: thin bits go."""
    kept: set[Cell] = set()
    for cell in cells:
        square = [Cell(cell.x + i, cell.y + j) for i in range(width) for j in range(width)]
        if all(c in cells for c in square):
            kept.update(square)
    return frozenset(kept)
