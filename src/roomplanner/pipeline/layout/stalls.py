"""Stalls: public toilets get a row of cubicles along one wall, each a tiny room of its own.

A room whose catalog entry has `stalls:` is split after allocation: along its longest wall
that doesn't touch circulation (the entrance side stays free) a row of stall rooms is cut
off, each entered only from the rest of the room (an annex of it), which keeps the sinks.
With `sides: 2` (a coffin motel's pods) a row goes along each of two opposite walls and
the rest is the aisle between them. A stall has the size of its first template, if any.
Stalls are given up while the rest is smaller than `rest_area` (room for the sinks). If not
even one stall and a passage in front of it fit, the room becomes the single-toilet type
instead. Stalls have real walls and doors, so they block sight in the VTT exports.
"""

from __future__ import annotations

from roomplanner.geometry import Cell, Side, connected, largest_rectangle
from roomplanner.pipeline.base import PlannedRoom
from roomplanner.rules import Rules, StallRule


def carve_stalls(
    rooms: list[PlannedRoom],
    rules: Rules,
    outside: frozenset[Cell] = frozenset(),
    keep: frozenset[Cell] = frozenset(),
) -> list[PlannedRoom]:
    """`outside`: cells beyond the footprint that count as circulation (a lone room's street);
    `keep`: cells no stall may take (where an exterior door is planned)."""
    circulation = set(outside) | {
        cell
        for room in rooms
        if rules.spec(room.type).circulation or room.hub
        for cell in room.cells
    }
    result: list[PlannedRoom] = []
    for room in rooms:
        # A room entered only through its host (a cell block behind its sally port) keeps
        # its door to the host instead; its walls to the corridor may take stalls.
        reach = set(room.host.cells) if room.host is not None else circulation
        result += carve(room, rules, reach, keep, whole=True)
    return result


def carve(
    room: PlannedRoom,
    rules: Rules,
    circulation: set[Cell],
    keep: frozenset[Cell] = frozenset(),
    whole: bool = False,
) -> list[PlannedRoom]:
    """The room split into its rest and stalls (just the room if it has no `stalls:`).

    `whole`: the rest must stay one area (no corner of an irregular room cut off by a row).
    """
    rule = rules.spec(room.type).stalls
    if rule is None:
        return [room]
    spec = rules.spec(room.type)
    minimum = rules.spec(rule.rest or room.type).min_side
    # The passage must be as wide as the room left in front of the stalls requires.
    passage = max(rule.passage or 0, minimum)
    size = rules.stall_size(rule)
    return _split(room, rule, size, (passage, minimum), spec.door_width, circulation, keep, whole)


def _split(
    room: PlannedRoom,
    rule: StallRule,
    size: tuple[int, int],
    widths: tuple[int, int],
    door: int,
    circulation: set[Cell],
    keep: frozenset[Cell],
    whole: bool,
) -> list[PlannedRoom]:
    """`widths`: the passage kept free in front of the stalls and the least width of the rest
    anywhere (the room's own minimum side)."""
    passage, minimum = widths
    # An irregular room (wrapped round a hallway's end) gets its stalls in its largest
    # rectangle, against walls the room doesn't continue beyond.
    box = largest_rectangle(room.cells)
    x0, y0, x1, y1 = box
    walls = {side for side in Side if not any(c in room.cells for c in beyond(box, side))}
    layouts: list[tuple[Side, ...]] = [(side,) for side in Side]
    if rule.sides == 2:  # both rows if they fit, else one
        layouts = [(Side.N, Side.S), (Side.W, Side.E), *layouts]
    best: list[tuple[Side, frozenset[Cell]]] = []  # (wall, cells) of each stall
    # Fewer stalls rather than no space for the sinks.
    for limit in range(rule.max, 0, -1):
        for sides in layouts:
            across = y1 - y0 if sides[0] in (Side.N, Side.S) else x1 - x0
            if across < len(sides) * size[1] + passage or not walls.issuperset(sides):
                continue
            for strict in (False, True):
                ignored = circulation if strict else set[Cell]()
                found = [
                    (side, cells)
                    for side in sides
                    for cells in _row(room, size, passage, ignored | keep, side, box, limit)
                ]
                rest = room.cells.difference(*(cells for _, cells in found))
                # The room must still reach circulation through a door.
                if found and door_run(rest, circulation) >= door:
                    roomy = (
                        len(rest) >= rule.rest_area
                        and thinnest(rest, rest) >= minimum
                        and (not whole or connected(rest))
                    )
                    if len(found) > len(best) and roomy:
                        best = found
                    break
        if best:
            break
    if not best:
        if rule.single is None:
            return [room]
        return [PlannedRoom(rule.single, room.cells, room.unit, room.entry, room.host)]
    rest = room.cells.difference(*(cells for _, cells in best))
    host = PlannedRoom(rule.rest or room.type, rest, room.unit, room.entry, room.host)
    # Every door faces the passage, so the stalls all look alike (not into a flank where
    # the room wraps round the end of the row).
    return [
        host,
        *(
            PlannedRoom(rule.room, cells, room.unit, host=host, front=wall.opposite)
            for wall, cells in best
        ),
    ]


def _row(
    room: PlannedRoom,
    size: tuple[int, int],
    passage: int,
    circulation: set[Cell],
    side: Side,
    box: tuple[int, int, int, int],
    limit: int,
) -> list[frozenset[Cell]]:
    """Up to `limit` stalls along the wall on `side`, skipping places that touch circulation.

    Gaps between stalls too narrow to walk into are added to the stall beside them.
    """
    x0, y0, x1, y1 = box
    along = x1 - x0 if side in (Side.N, Side.S) else y1 - y0
    width, depth = size

    def cells(start: int, width: int) -> frozenset[Cell]:
        return frozenset(
            c
            for c in room.cells
            if x0 <= c.x < x1
            and y0 <= c.y < y1
            and start <= _along(c, side, x0, y0) < start + width
            and _depth(c, side, x0, y0, x1, y1) < depth
        )

    def free(block: frozenset[Cell]) -> bool:
        return not any(c.neighbour(s) in circulation for c in block for s in Side)

    spans: list[tuple[int, int]] = []  # (start, width)
    start = 0
    while start + width <= along and len(spans) < limit:
        if free(cells(start, width)):
            spans.append((start, width))
            start += width
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


def door_run(cells: frozenset[Cell], circulation: set[Cell]) -> int:
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


def beyond(box: tuple[int, int, int, int], side: Side) -> list[Cell]:
    """The cells just outside the box on `side`."""
    x0, y0, x1, y1 = box
    match side:
        case Side.N:
            return [Cell(x, y0 - 1) for x in range(x0, x1)]
        case Side.S:
            return [Cell(x, y1) for x in range(x0, x1)]
        case Side.W:
            return [Cell(x0 - 1, y) for y in range(y0, y1)]
        case Side.E:
            return [Cell(x1, y) for y in range(y0, y1)]


def thinnest(cells: frozenset[Cell], space: frozenset[Cell]) -> int:
    """Smallest straight extent of `space` through any of `cells`, across or along."""

    def run(cell: Cell, side: Side) -> int:
        n = 1
        for direction in (side, side.opposite):
            c = cell.neighbour(direction)
            while c in space:
                n += 1
                c = c.neighbour(direction)
        return n

    return min(min(run(c, Side.E), run(c, Side.S)) for c in cells)
