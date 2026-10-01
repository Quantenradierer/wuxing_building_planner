"""Cluster hallways end after their last door; the rest joins a room beside it.

A cluster's side hallway runs from the corridor to the far side of its strip. Past the
door of its deepest room it leads nowhere, so after allocation it is cut back to the last
place a room it serves still needs: every room that has no other way to circulation keeps a
straight wall along the hallway as long as before, up to its door plus a cell either side.
The cut-off end joins the neighbour it shares the longest wall with, if it stays within its
size limit; that room then wraps round the hallway's end (an L). Cells where the layout
wants an exterior door stay hallway.
"""

from __future__ import annotations

from dataclasses import replace

from roomplanner.geometry import Cell, Side
from roomplanner.pipeline.base import PlannedRoom
from roomplanner.pipeline.layout.allocation import ABSORB_TOLERANCE
from roomplanner.pipeline.layout.stalls import beyond, carve, door_run, thinnest
from roomplanner.rules import Rules
from roomplanner.validation import MIN_ALCOVE


def trim_hallways(
    rooms: list[PlannedRoom], rules: Rules, keep: frozenset[Cell] = frozenset()
) -> list[PlannedRoom]:
    """`keep`: cells that must stay hallway (exterior door hints)."""
    result = list(rooms)
    for hallway in [r for r in rooms if r.hallway]:
        result = _trim(hallway, result, rules, keep)
    return result


def _trim(
    hallway: PlannedRoom, rooms: list[PlannedRoom], rules: Rules, keep: frozenset[Cell]
) -> list[PlannedRoom]:
    box = _box(hallway.cells)
    if box is None:
        return rooms
    circulation = {
        c
        for r in rooms
        if r is not hallway and (rules.spec(r.type).circulation or r.hub)
        for c in r.cells
    }
    roots = [s for s in Side if any(c in circulation for c in beyond(box, s))]
    if len(roots) != 1:
        return rooms
    far = roots[0].opposite
    cells = set(hallway.cells)
    # Rooms that reach circulation only through the hallway, and the wall each one needs.
    needs: list[tuple[frozenset[Cell], int]] = []
    for room in rooms:
        if room is hallway or room.host is not None or (room.unit and not room.entry):
            continue
        spec = rules.spec(room.type)
        if spec.circulation or room.hub:
            continue
        along = door_run(room.cells, cells)
        if along == 0 or door_run(room.cells, circulation) >= spec.door_width:
            continue
        needs.append((room.cells, min(along, spec.door_width + 2)))

    def serves(rest: set[Cell]) -> bool:
        return all(door_run(c, rest) >= need for c, need in needs)

    rows = _rows(box, far)
    cut = 0
    # The whole hallway goes if no room needs it (they all reach the corridor directly).
    while cut < len(rows):
        row = rows[cut]
        rest = cells.difference(*rows[: cut + 1])
        if row & keep or not serves(rest):
            break
        cut += 1
    # The longest end some room may take (fewer rows if the room would grow too big).
    for n in range(cut, 0, -1):
        piece = frozenset[Cell]().union(*rows[:n])
        taker = _taker(piece, rooms, hallway, circulation | (cells - piece), rules)
        if taker is not None:
            result = [r for r in rooms]
            result[result.index(taker)] = replace(taker, cells=taker.cells | piece)
            if piece == hallway.cells:
                result.remove(hallway)
            else:
                result[result.index(hallway)] = replace(hallway, cells=hallway.cells - piece)
            return result
    return rooms


def _taker(
    piece: frozenset[Cell],
    rooms: list[PlannedRoom],
    hallway: PlannedRoom,
    circulation: set[Cell],
    rules: Rules,
) -> PlannedRoom | None:
    """The neighbour sharing the longest wall with the piece that may grow by it."""
    fixed = {e.room for e in rules.program.core}
    hosts = {id(r.host) for r in rooms if r.host is not None}
    options: list[tuple[int, float, PlannedRoom]] = []
    for room in rooms:
        if room is hallway or room.unit is not None or room.host is not None or room.hub:
            continue
        spec = rules.spec(room.type)
        if spec.circulation or room.type in fixed or id(room) in hosts:
            continue
        shared = door_run(room.cells, set(piece))
        grown = replace(room, cells=room.cells | piece)
        if not shared or thinnest(piece, grown.cells) < min(MIN_ALCOVE, spec.min_side):
            continue
        area = len(grown.cells)
        if spec.stalls is not None:
            # A public toilet keeps its stalls; its size counts without them (rooms of
            # their own).
            before, after = carve(room, rules, circulation), carve(grown, rules, circulation)
            if len(after) < len(before):
                continue
            area = len(after[0].cells)
        if area > spec.area[1] * ABSORB_TOLERANCE:
            continue
        options.append((-shared, area / spec.area[1], room))
    if not options:
        return None
    return min(options, key=lambda o: (o[0], o[1]))[2]


def _box(cells: frozenset[Cell]) -> tuple[int, int, int, int] | None:
    xs, ys = [c.x for c in cells], [c.y for c in cells]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs) + 1, max(ys) + 1
    return (x0, y0, x1, y1) if len(cells) == (x1 - x0) * (y1 - y0) else None


def _rows(box: tuple[int, int, int, int], side: Side) -> list[frozenset[Cell]]:
    """The box's rows across, starting at the edge on `side`."""
    x0, y0, x1, y1 = box
    match side:
        case Side.N:
            return [frozenset(Cell(x, y) for x in range(x0, x1)) for y in range(y0, y1)]
        case Side.S:
            return [frozenset(Cell(x, y) for x in range(x0, x1)) for y in reversed(range(y0, y1))]
        case Side.W:
            return [frozenset(Cell(x, y) for y in range(y0, y1)) for x in range(x0, x1)]
        case Side.E:
            return [frozenset(Cell(x, y) for y in range(y0, y1)) for x in reversed(range(x0, x1))]
