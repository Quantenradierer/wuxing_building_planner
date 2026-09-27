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
        spec = rules.spec(room.type)
        # The passage must be as wide as the room left in front of the stalls requires.
        passage = max(rule.passage or 0, rules.spec(rule.rest or room.type).min_side)
        result += _split(room, rule, passage, spec.door_width, circulation)
    return result


def _split(
    room: PlannedRoom, rule: StallRule, passage: int, door: int, circulation: set[Cell]
) -> list[PlannedRoom]:
    """`passage`: the room's own minimum side, kept free in front of the stalls."""
    xs, ys = [c.x for c in room.cells], [c.y for c in room.cells]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs) + 1, max(ys) + 1
    if len(room.cells) != (x1 - x0) * (y1 - y0):  # only plain rectangles
        return [PlannedRoom(rule.single, room.cells, room.unit, room.entry, room.host)]
    box = (x0, y0, x1, y1)
    best: list[frozenset[Cell]] = []
    wall = Side.N
    for side in Side:
        across = y1 - y0 if side in (Side.N, Side.S) else x1 - x0
        if across < rule.depth + passage:
            continue
        for strict in (False, True):
            found = _row(room, rule, passage, circulation if strict else set(), side, box)
            # The room must still reach circulation through a door.
            if found and _door_run(room.cells.difference(*found), circulation) >= door:
                if len(found) > len(best):
                    best, wall = found, side
                break
    if not best:
        return [PlannedRoom(rule.single, room.cells, room.unit, room.entry, room.host)]
    rest = room.cells.difference(*best)
    host = PlannedRoom(rule.rest or room.type, rest, room.unit, room.entry, room.host)
    # Every door faces the passage, so the stalls all look alike (not into a flank where
    # the room wraps round the end of the row).
    front = wall.opposite
    return [
        host,
        *(PlannedRoom(rule.room, cells, room.unit, host=host, front=front) for cells in best),
    ]


def _row(
    room: PlannedRoom,
    rule: StallRule,
    passage: int,
    circulation: set[Cell],
    side: Side,
    box: tuple[int, int, int, int],
) -> list[frozenset[Cell]]:
    """Stalls along the wall on `side`, skipping places that touch circulation (doors).

    Gaps between stalls too narrow to walk into are added to the stall beside them.
    """
    x0, y0, x1, y1 = box
    along = x1 - x0 if side in (Side.N, Side.S) else y1 - y0

    def cells(start: int, width: int) -> frozenset[Cell]:
        return frozenset(
            c
            for c in room.cells
            if start <= _along(c, side, x0, y0) < start + width
            and _depth(c, side, x0, y0, x1, y1) < rule.depth
        )

    def free(block: frozenset[Cell]) -> bool:
        return not any(c.neighbour(s) in circulation for c in block for s in Side)

    spans: list[tuple[int, int]] = []  # (start, width)
    start = 0
    while start + rule.width <= along and len(spans) < rule.max:
        if free(cells(start, rule.width)):
            spans.append((start, rule.width))
            start += rule.width
        else:
            start += 1
    # Close gaps too narrow to use (to the neighbouring stall), or give the stall up.
    changed = True
    while changed and spans:
        changed = False
        bounds = [0, *(p for s0, w in spans for p in (s0, s0 + w)), along]
        for k in range(len(spans) + 1):
            gap = bounds[2 * k + 1] - bounds[2 * k]
            if 0 < gap < passage:
                i = k - 1 if k > 0 else 0  # the stall before the gap, else the one after it
                s0, w = spans[i]
                grown = (s0, w + gap) if i == k - 1 else (s0 - gap, w + gap)
                if free(cells(*grown)):
                    spans[i] = grown
                else:
                    spans.pop(i)
                changed = True
                break
    return [cells(s0, w) for s0, w in spans]


def _door_run(cells: frozenset[Cell], circulation: set[Cell]) -> int:
    """Longest straight run of walls between these cells and circulation."""
    longest = 0
    for side in Side:
        # (line across the wall, position along it) of each wall towards circulation
        spots = sorted(
            (c.y, c.x) if side in (Side.N, Side.S) else (c.x, c.y)
            for c in cells
            if c.neighbour(side) in circulation
        )
        run = 0
        for k, (line, pos) in enumerate(spots):
            run = run + 1 if k and spots[k - 1] == (line, pos - 1) else 1
            longest = max(longest, run)
    return longest


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
