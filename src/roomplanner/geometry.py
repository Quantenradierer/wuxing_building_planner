"""Grid primitives. See docs/architecture.md, section "Coordinates"."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

CELL_SIZE_M = 0.5
CELL_AREA_M2 = CELL_SIZE_M * CELL_SIZE_M


class Side(StrEnum):
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


class Axis(StrEnum):
    H = "h"
    V = "v"


@dataclass(frozen=True, order=True, slots=True)
class Cell:
    x: int
    y: int

    def neighbour(self, side: Side) -> Cell:
        dx, dy = side.delta
        return Cell(self.x + dx, self.y + dy)


@dataclass(frozen=True, order=True, slots=True)
class Edge:
    """Unit segment starting at grid vertex (x, y).

    Axis.H runs east to (x+1, y): the north side of cell (x, y).
    Axis.V runs south to (x, y+1): the west side of cell (x, y).
    """

    x: int
    y: int
    axis: Axis

    @staticmethod
    def of(cell: Cell, side: Side) -> Edge:
        match side:
            case Side.N:
                return Edge(cell.x, cell.y, Axis.H)
            case Side.S:
                return Edge(cell.x, cell.y + 1, Axis.H)
            case Side.W:
                return Edge(cell.x, cell.y, Axis.V)
            case Side.E:
                return Edge(cell.x + 1, cell.y, Axis.V)

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


def meters_to_cells(meters: float) -> int:
    """Snap a length to the grid (round half up)."""
    return int(meters / CELL_SIZE_M + 0.5)


def rectangle(x: int, y: int, width: int, height: int) -> frozenset[Cell]:
    return frozenset(Cell(cx, cy) for cx in range(x, x + width) for cy in range(y, y + height))


def boundary_edges(cells: frozenset[Cell]) -> frozenset[Edge]:
    """Edges separating the given cells from everything else."""
    return frozenset(
        Edge.of(cell, side) for cell in cells for side in Side if cell.neighbour(side) not in cells
    )
