"""Stalls: public toilets get a row of cubicles along one wall, each a tiny room of its own.

A room whose catalog entry has `stalls:` is split after allocation: along its longest wall
that doesn't touch circulation (the entrance side stays free) a row of stall rooms is cut
off, each entered only from the rest of the room (an annex of it), which keeps the sinks.
If not even one stall and a passage in front of it fit, the room becomes the single-toilet
type instead. Stalls have real walls and doors, so they block sight in the VTT exports.
"""

from __future__ import annotations

from roomplanner.geometry import Cell, Side
from roomplanner.pipeline.base import PlannedRoom
from roomplanner.rules import Rules, StallRule


def carve_stalls(rooms: list[PlannedRoom], rules: Rules) -> list[PlannedRoom]:
    circulation = {
        cell for room in rooms if rules.spec(room.type).circulation for cell in room.cells
    }
    result: list[PlannedRoom] = []
    for room in rooms:
        rule = rules.spec(room.type).stalls
        if rule is None:
            result.append(room)
            continue
        result += _split(room, rule, rules.spec(room.type).min_side, circulation)
    return result


def _split(
    room: PlannedRoom, rule: StallRule, passage: int, circulation: set[Cell]
) -> list[PlannedRoom]:
    """`passage`: the room's own minimum side, kept free in front of the stalls."""
    xs, ys = [c.x for c in room.cells], [c.y for c in room.cells]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs) + 1, max(ys) + 1
    if len(room.cells) != (x1 - x0) * (y1 - y0):  # only plain rectangles
        return [PlannedRoom(rule.single, room.cells, room.unit, room.entry, room.host)]
    options: list[tuple[int, Side]] = []
    for side in Side:
        along = x1 - x0 if side in (Side.N, Side.S) else y1 - y0
        across = y1 - y0 if side in (Side.N, Side.S) else x1 - x0
        wall = [c for c in room.cells if c.neighbour(side) not in room.cells]
        if any(c.neighbour(side) in circulation for c in wall):
            continue  # the entrance side
        if along >= rule.width and across >= rule.depth + passage:
            options.append((along, side))
    if not options:
        return [PlannedRoom(rule.single, room.cells, room.unit, room.entry, room.host)]
    along, side = max(options, key=lambda o: (o[0], o[1].value))
    count = min(rule.max, along // rule.width)
    widths = [rule.width] * count
    leftover = along - sum(widths)
    if leftover < passage:  # too thin to walk along: the stalls get a bit wider instead
        for i in range(leftover):
            widths[i % count] += 1
    rest = room.cells
    stalls: list[frozenset[Cell]] = []
    start = 0
    for width in widths:
        cells = frozenset(
            c
            for c in room.cells
            if start <= _along(c, side, x0, y0) < start + width
            and _depth(c, side, x0, y0, x1, y1) < rule.depth
        )
        start += width
        stalls.append(cells)
        rest -= cells
    host = PlannedRoom(room.type, rest, room.unit, room.entry, room.host)
    return [host, *(PlannedRoom(rule.room, cells, room.unit, host=host) for cells in stalls)]


def _along(cell: Cell, side: Side, x0: int, y0: int) -> int:
    return cell.x - x0 if side in (Side.N, Side.S) else cell.y - y0


def _depth(cell: Cell, side: Side, x0: int, y0: int, x1: int, y1: int) -> int:
    """Distance of the cell from the wall on `side`, in cells."""
    match side:
        case Side.N:
            return cell.y - y0
        case Side.S:
            return y1 - 1 - cell.y
        case Side.W:
            return cell.x - x0
        case Side.E:
            return x1 - 1 - cell.x
