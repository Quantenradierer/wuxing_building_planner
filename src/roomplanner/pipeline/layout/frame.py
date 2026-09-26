"""Local coordinates for rectangular parts of a footprint.

A frame covers one rectangle. `u` runs along its long axis (or away from a junction for
wings), `v` across it. Layout code works in (u, v) and never cares about orientation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from roomplanner.geometry import Cell, Side


class LocalSide(StrEnum):
    U0 = "u0"  # short end at u = 0
    U1 = "u1"  # short end at u = length
    V0 = "v0"  # long facade at v = 0
    V1 = "v1"  # long facade at v = depth

    @property
    def is_end(self) -> bool:
        return self in (LocalSide.U0, LocalSide.U1)


@dataclass(frozen=True)
class Box:
    """Axis-aligned cell rectangle in absolute coordinates, [x0, x1) x [y0, y1)."""

    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def centre(self) -> tuple[float, float]:
        return (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2

    def gap(self, other: Box) -> int:
        """Manhattan distance between the boxes (0 if they touch or overlap)."""
        dx = max(0, other.x0 - self.x1, self.x0 - other.x1)
        dy = max(0, other.y0 - self.y1, self.y0 - other.y1)
        return dx + dy


@dataclass(frozen=True)
class Frame:
    u_is_x: bool
    length: int
    depth: int
    x0: int = 0
    y0: int = 0
    flip_u: bool = False  # u runs towards smaller x/y

    @staticmethod
    def for_rect(box: Box) -> Frame:
        """Frame along the long axis of a rectangle."""
        width, height = box.x1 - box.x0, box.y1 - box.y0
        if width >= height:
            return Frame(True, width, height, box.x0, box.y0)
        return Frame(False, height, width, box.x0, box.y0)

    def cell(self, u: int, v: int) -> Cell:
        if self.flip_u:
            u = self.length - 1 - u
        return Cell(self.x0 + u, self.y0 + v) if self.u_is_x else Cell(self.x0 + v, self.y0 + u)

    def to_local(self, x: float, y: float) -> tuple[float, float]:
        """Continuous absolute position to (u, v); inverse of `cell` for cell corners."""
        u, v = (x - self.x0, y - self.y0) if self.u_is_x else (y - self.y0, x - self.x0)
        return (self.length - u if self.flip_u else u), v

    def rect(self, u0: int, u1: int, v0: int, v1: int) -> frozenset[Cell]:
        return frozenset(self.cell(u, v) for u in range(u0, u1) for v in range(v0, v1))

    def box(self, u0: int, u1: int, v0: int, v1: int) -> Box:
        corners = [self.cell(u0, v0), self.cell(u1 - 1, v1 - 1)]
        xs, ys = [c.x for c in corners], [c.y for c in corners]
        return Box(min(xs), min(ys), max(xs) + 1, max(ys) + 1)

    def local(self, side: Side) -> LocalSide:
        if self.u_is_x:
            mapping = {Side.W: LocalSide.U0, Side.E: LocalSide.U1, Side.N: LocalSide.V0}
        else:
            mapping = {Side.N: LocalSide.U0, Side.S: LocalSide.U1, Side.W: LocalSide.V0}
        local = mapping.get(side, LocalSide.V1)
        if self.flip_u and local.is_end:
            return LocalSide.U1 if local is LocalSide.U0 else LocalSide.U0
        return local

    def side(self, local: LocalSide) -> Side:
        return next(s for s in Side if self.local(s) is local)

    def absolute_grid_offset(self, grid: Grid) -> int:
        """Grid offset along u expressed as an absolute x (or y) coordinate modulo the module."""
        origin = self.x0 if self.u_is_x else self.y0
        if self.flip_u:
            return (origin + self.length - grid.offset) % grid.module
        return (origin + grid.offset) % grid.module


@dataclass(frozen=True)
class Grid:
    """Facade module grid along u: partition walls on long facades sit on grid points."""

    module: int
    offset: int
    length: int

    @staticmethod
    def centred(module: int, length: int) -> Grid:
        return Grid(module, (length % module) // 2, length)

    def floor(self, u: int) -> int:
        """Largest grid point <= u (or 0)."""
        return max(0, self.offset + math.floor((u - self.offset) / self.module) * self.module)

    def ceil(self, u: int) -> int:
        """Smallest grid point >= u (or length)."""
        return min(
            self.length, self.offset + math.ceil((u - self.offset) / self.module) * self.module
        )

    def round_up(self, width: int) -> int:
        """A width rounded up to whole modules."""
        return max(1, math.ceil(width / self.module)) * self.module

    def points(self) -> list[int]:
        return list(range(self.offset, self.length + 1, self.module))


class BandKind(StrEnum):
    STRIP = "strip"
    CORRIDOR = "corridor"


@dataclass(frozen=True)
class Band:
    """A full-length slice of the building across v."""

    index: int
    kind: BandKind
    v0: int
    v1: int
    corridor_at: LocalSide | None = None  # strips: side (V0/V1) where the serving corridor is
    facade: bool = False  # strips: touches a long facade

    @property
    def depth(self) -> int:
        return self.v1 - self.v0


@dataclass(frozen=True)
class Interval:
    u0: int
    u1: int

    @property
    def width(self) -> int:
        return self.u1 - self.u0

    @property
    def centre(self) -> float:
        return (self.u0 + self.u1) / 2

    def overlaps(self, other: Interval) -> bool:
        return self.u0 < other.u1 and other.u0 < self.u1

    def gap_to(self, other: Interval) -> int:
        return max(0, other.u0 - self.u1, self.u0 - other.u1)


def free_intervals(length: int, blocked: list[Interval]) -> list[Interval]:
    result: list[Interval] = []
    position = 0
    for interval in sorted(blocked, key=lambda i: i.u0):
        if interval.u0 > position:
            result.append(Interval(position, interval.u0))
        position = max(position, interval.u1)
    if position < length:
        result.append(Interval(position, length))
    return result
