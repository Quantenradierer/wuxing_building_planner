"""Units (flats) in the partition layout: a region typed as a unit is subdivided.

The assigner places a unit like any room. Afterwards the largest rectangle of its region is
cut with `units.subdivide`, seen from the side that touches circulation the most (that side
is the unit's "corridor"); what does not fit the rectangle becomes leftover for the
neighbours. A try whose entry hall has no door onto circulation is repeated, flipped, and
then narrower and elsewhere along the side; a unit that never gets an entry becomes
leftover.
"""

from __future__ import annotations

import random
from dataclasses import replace

from roomplanner.geometry import Cell, Side, largest_rectangle
from roomplanner.pipeline.base import PlannedRoom
from roomplanner.pipeline.layout.frame import Band, BandKind, Frame, Interval, LocalSide
from roomplanner.pipeline.layout.regions import components, contact
from roomplanner.pipeline.layout.units import subdivide
from roomplanner.rules import Rules, UnitSpec

TRIES = 2  # subdivisions tried per position (the unit is flipped at random)


def split_units(
    rooms: list[PlannedRoom], rules: Rules, access: frozenset[Cell], level: int, rng: random.Random
) -> list[PlannedRoom]:
    units = rules.program.units
    result: list[PlannedRoom] = []
    count = 0
    for room in rooms:
        spec = units.get(room.type)
        if spec is None or room.unit is not None or room.host is not None:
            result.append(room)
            continue
        x0, y0, x1, y1 = largest_rectangle(room.cells)
        box = frozenset(Cell(x, y) for x in range(x0, x1) for y in range(y0, y1))
        sides = sorted(Side, key=lambda s: -_contact(box, s, access))
        side = sides[0]
        if _contact(box, side, access) == 0:
            result.append(replace(room, leftover=True, type=_filler(rules)))
            continue
        count += 1
        name = f"{level}-{count:02d}"
        along_x = side in (Side.N, Side.S)
        frame = Frame(along_x, *((x1 - x0, y1 - y0) if along_x else (y1 - y0, x1 - x0)), x0, y0)
        band = Band(0, BandKind.STRIP, 0, frame.depth, frame.local(side), facade=True)
        span = Interval(0, frame.length)
        parts: list[PlannedRoom] = []
        for width in _widths(frame.length, spec, rules):
            for start in _starts(frame, side, box, access, width):
                span = Interval(start, start + width)
                for _ in range(TRIES):
                    parts = subdivide(frame, band, span, name, spec, rules, rng)
                    if _enters(parts, rules, access):
                        break
                else:
                    continue
                break
            else:
                continue
            break
        else:
            result.append(replace(room, cells=room.cells, leftover=True, type=_filler(rules)))
            count -= 1
            continue
        result += parts
        rest = room.cells - frozenset(c for p in parts for c in p.cells)
        for piece in components(rest):
            result.append(replace(room, cells=piece, leftover=True, type=_filler(rules)))
    return result


def _widths(length: int, spec: UnitSpec, rules: Rules) -> list[int]:
    """The unit's width along its corridor: all of it first, then narrower."""
    low = max(rules.spec(k).min_side for k in [*spec.back, *spec.front]) + spec.hall_width
    options = [length, round(length * 0.75), round(length * 0.55), low + 2]
    return sorted({w for w in options if low <= w <= length}, reverse=True)


def _starts(
    frame: Frame, side: Side, box: frozenset[Cell], access: frozenset[Cell], width: int
) -> list[int]:
    """Offsets of a unit of `width` along the side, those nearest the corridor first."""
    touching = [
        u
        for u in range(frame.length)
        if frame.cell(u, 0 if frame.local(side) is LocalSide.V0 else frame.depth - 1).neighbour(
            side
        )
        in access
    ]
    middle = sum(touching) / len(touching) if touching else frame.length / 2
    return sorted(range(frame.length - width + 1), key=lambda s: abs(s + width / 2 - middle))


def _filler(rules: Rules) -> str:
    return rules.program.cluster_filler


def _contact(box: frozenset[Cell], side: Side, access: frozenset[Cell]) -> int:
    edge = [c for c in box if c.neighbour(side) not in box]
    return sum(1 for c in edge if c.neighbour(side) in access)


def _enters(parts: list[PlannedRoom], rules: Rules, access: frozenset[Cell]) -> bool:
    entry = next((p for p in parts if p.entry), None)
    return entry is not None and contact(entry.cells, access) >= rules.spec(entry.type).door_width
