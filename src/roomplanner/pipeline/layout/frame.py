"""Local coordinates for rectangular layouts.

`u` runs along the building's long axis, `v` across it. Layout code works in (u, v) and
never cares whether the building is wider than deep.
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
class Frame:
    long_is_x: bool
    length: int
    depth: int

    @staticmethod
    def for_size(width: int, height: int) -> Frame:
        if width >= height:
            return Frame(True, width, height)
        return Frame(False, height, width)

    def cell(self, u: int, v: int) -> Cell:
        return Cell(u, v) if self.long_is_x else Cell(v, u)

    def rect(self, u0: int, u1: int, v0: int, v1: int) -> frozenset[Cell]:
        return frozenset(self.cell(u, v) for u in range(u0, u1) for v in range(v0, v1))

    def local(self, side: Side) -> LocalSide:
        if self.long_is_x:
            return {Side.W: LocalSide.U0, Side.E: LocalSide.U1, Side.N: LocalSide.V0}.get(
                side, LocalSide.V1
            )
        return {Side.N: LocalSide.U0, Side.S: LocalSide.U1, Side.W: LocalSide.V0}.get(
            side, LocalSide.V1
        )

    def side(self, local: LocalSide) -> Side:
        return next(s for s in Side if self.local(s) is local)


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
