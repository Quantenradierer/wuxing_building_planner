"""Grid primitives. See docs/architecture.md, section "Coordinates"."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Collection, Iterator
from collections.abc import Set as AbstractSet
from enum import EnumType, StrEnum
from functools import lru_cache
from typing import Any, NamedTuple, cast

# Physical scale, only for renderers. Everything else (parameters, rules, output) is in cells.
CELL_SIZE_M = 0.5


class _FastIteration(EnumType):
    """Iterating a Side is in the hot loops of every stage; the stock version is a generator."""

    def __iter__(cls) -> Iterator[Any]:
        try:
            return iter(cls.__dict__["_ordered"])
        except KeyError:
            ordered = tuple(cls._member_map_.values())
            cls._ordered = ordered  # pyright: ignore[reportAttributeAccessIssue]
            return iter(ordered)


class Side(StrEnum, metaclass=_FastIteration):
    N = "N"
    E = "E"
    S = "S"
    W = "W"

    @property
    def opposite(self) -> Side:
        return _OPPOSITE[self]

    @property
    def axis(self) -> Axis:
        """Axis of the edges lying on this side of a cell."""
        return Axis.H if self in (Side.N, Side.S) else Axis.V

    @property
    def delta(self) -> tuple[int, int]:
        return _DELTA[self]


_OPPOSITE = {Side.N: Side.S, Side.S: Side.N, Side.E: Side.W, Side.W: Side.E}
_DELTA = {Side.N: (0, -1), Side.S: (0, 1), Side.E: (1, 0), Side.W: (-1, 0)}
_new = cast(Callable[[type, tuple[Any, ...]], Any], tuple.__new__)  # pyright: ignore[reportUnknownMemberType]


class Axis(StrEnum):
    H = "h"
    V = "v"


# (dx, dy, axis) of the edge on each side of a cell, relative to the cell's own vertex
_SIDE_EDGE = {
    Side.N: (0, 0, Axis.H),
    Side.S: (0, 1, Axis.H),
    Side.W: (0, 0, Axis.V),
    Side.E: (1, 0, Axis.V),
}


class Cell(NamedTuple):
    x: int
    y: int

    def neighbour(self, side: Side) -> Cell:
        dx, dy = _DELTA[side]
        # tuple.__new__ skips the NamedTuple's Python-level constructor; this runs millions of times
        return _new(Cell, (self[0] + dx, self[1] + dy))


class Edge(NamedTuple):
    """Unit segment starting at grid vertex (x, y).

    Axis.H runs east to (x+1, y): the north side of cell (x, y).
    Axis.V runs south to (x, y+1): the west side of cell (x, y).
    """

    x: int
    y: int
    axis: Axis

    @staticmethod
    def of(cell: Cell, side: Side) -> Edge:
        dx, dy, axis = _SIDE_EDGE[side]
        return _new(Edge, (cell[0] + dx, cell[1] + dy, axis))

    @staticmethod
    def between(a: Cell, b: Cell) -> Edge:
        for side in Side:
            if a.neighbour(side) == b:
                return Edge.of(a, side)
        raise ValueError(f"cells {a} and {b} are not adjacent")

    def cells(self) -> tuple[Cell, Cell]:
        """The two cells separated by this edge: (north, south) or (west, east)."""
        if self.axis is Axis.H:
            return Cell(self.x, self.y - 1), Cell(self.x, self.y)
        return Cell(self.x - 1, self.y), Cell(self.x, self.y)

    def next_along(self) -> Edge:
        """The following edge on the same line (east for H, south for V)."""
        if self.axis is Axis.H:
            return Edge(self.x + 1, self.y, self.axis)
        return Edge(self.x, self.y + 1, self.axis)


def rectangle(x: int, y: int, width: int, height: int) -> frozenset[Cell]:
    return frozenset(Cell(cx, cy) for cx in range(x, x + width) for cy in range(y, y + height))


def largest_rectangle(cells: frozenset[Cell]) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) of the largest rectangle of the cells (the room itself if it is one)."""
    xs, ys = [c.x for c in cells], [c.y for c in cells]
    bx0, by0, bx1, by1 = min(xs), min(ys), max(xs) + 1, max(ys) + 1
    best = (0, (bx0, by0, bx0 + 1, by0 + 1))
    heights = [0] * (bx1 - bx0)
    for y in range(by0, by1):
        heights = [h + 1 if Cell(bx0 + i, y) in cells else 0 for i, h in enumerate(heights)]
        for i in range(len(heights)):
            low = heights[i]
            for j in range(i, len(heights)):
                low = min(low, heights[j])
                if low == 0:
                    break
                area = low * (j - i + 1)
                if area > best[0]:
                    best = (area, (bx0 + i, y + 1 - low, bx0 + j + 1, y + 1))
    return best[1]


@lru_cache(maxsize=16)
def boundary_edges(cells: frozenset[Cell]) -> frozenset[Edge]:
    """Edges separating the given cells from everything else."""
    return frozenset(
        Edge.of(cell, side) for cell in cells for side in Side if cell.neighbour(side) not in cells
    )


def connected(cells: frozenset[Cell] | set[Cell]) -> bool:
    """True if the cells form one 4-connected area (and there is at least one)."""
    free = {(c.x, c.y) for c in cells}
    if not free:
        return False
    start = min(free)
    seen = {start}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        for neighbour in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if neighbour in free and neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return len(seen) == len(free)


def thinnest_extent(cells: Collection[Cell], space: AbstractSet[Cell]) -> int:
    """Smallest straight extent of `space` through any of `cells`, across or along."""
    # Every cell of one straight stretch of `space` has the same extent: measure it once.
    memo: dict[tuple[int, int, int, int], int] = {}

    def run(x: int, y: int, dx: int, dy: int) -> int:
        if (x, y, dx, dy) in memo:
            return memo[x, y, dx, dy]
        members = [(x, y)]
        for sign in (1, -1):
            px, py = x + sign * dx, y + sign * dy
            while (px, py) in space:
                members.append((px, py))
                px, py = px + sign * dx, py + sign * dy
        for mx, my in members if (x, y) in space else ():
            memo[mx, my, dx, dy] = len(members)
        return len(members)

    return min(min(run(c[0], c[1], 1, 0), run(c[0], c[1], 0, 1)) for c in cells)
