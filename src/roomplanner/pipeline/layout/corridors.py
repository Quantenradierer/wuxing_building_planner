"""Corridor cells that nobody needs go to a room beside them.

A corridor along a lobby is redundant: the lobby is circulation too. After allocation every
room whose whole wall on such a corridor faces the lobby grows over the corridor's depth, so
the room opens into the lobby and the corridor band shrinks to what the other rooms still
use. The union must stay a rectangle within the room's size limit, circulation must stay one
piece, and exterior door cells stay corridor.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from roomplanner.geometry import Cell, Side
from roomplanner.pipeline.base import PlannedRoom
from roomplanner.pipeline.layout.allocation import ABSORB_TOLERANCE
from roomplanner.pipeline.layout.hallways import _taker  # pyright: ignore[reportPrivateUsage]
from roomplanner.pipeline.layout.leftovers import _box  # pyright: ignore[reportPrivateUsage]
from roomplanner.pipeline.layout.stalls import beyond, door_run, thinnest
from roomplanner.rules import Rules

MAX_SWAPS = 2  # rooms moved into pockets per floor
MAX_TRIES = 8  # pockets tried per moved room
MAX_SHAPES = 6  # rectangle shapes tried per moved room


@dataclass(frozen=True)
class _Floor:
    """The circulation cells of a floor: corridor, other circulation (lobby, halls), and the
    side hallways of clusters."""

    corridor: frozenset[Cell]
    other: frozenset[Cell]
    circulation: frozenset[Cell]

    @staticmethod
    def of(rooms: list[PlannedRoom], rules: Rules) -> _Floor:
        corridor = {c for r in rooms if r.type == "corridor" and not r.hallway for c in r.cells}
        other = {
            c
            for r in rooms
            if r.type != "corridor" and (rules.spec(r.type).circulation or r.hub)
            for c in r.cells
        }
        halls = {c for r in rooms if r.hallway for c in r.cells}
        return _Floor(frozenset(corridor), frozenset(other), frozenset(corridor | other | halls))

    @property
    def folds(self) -> bool:
        """A lobby (other circulation) lies beside a corridor."""
        return bool(self.corridor) and bool(self.other)


def fold_corridors(
    rooms: list[PlannedRoom], rules: Rules, keep: frozenset[Cell] = frozenset()
) -> list[PlannedRoom]:
    """`keep`: cells that must stay corridor (exterior door hints)."""
    result = list(rooms)
    floor = _Floor.of(result, rules)
    if not floor.folds:
        return result
    fixed = {e.room for e in rules.program.core}
    hosts = {id(r.host) for r in result if r.host is not None}
    for room in rooms:
        spec = rules.spec(room.type)
        if (
            spec.circulation
            or spec.stalls is not None
            or room.hub
            or room.hallway
            or room.type in fixed
            or id(room) in hosts
            or room.unit is not None
            or room.host is not None
        ):
            continue
        box = _box(room.cells)
        if box is None:
            continue
        for side in Side:
            piece = _piece(box, side, floor)
            if not piece or piece & keep:
                continue
            grown = replace(room, cells=room.cells | piece)
            if len(grown.cells) > spec.area[1] * ABSORB_TOLERANCE:
                continue
            if _fits(result, {id(room): grown}, floor, piece, rules):
                result = _carve(result, {id(room): grown}, piece)
                floor = _Floor.of(result, rules)
                break
    return result


def fill_pockets(
    rooms: list[PlannedRoom], rules: Rules, keep: frozenset[Cell] = frozenset()
) -> list[PlannedRoom]:
    """Move a small back room into a corridor pocket beside the lobby.

    What the fold leaves of the corridor along a lobby is a bay nobody walks through. A small
    back room (storage, copy room) from elsewhere on the floor takes the bay, and the cells
    it leaves join a neighbour, which becomes a larger room.
    """
    result = list(rooms)
    floor = _Floor.of(result, rules)
    if not floor.folds:
        return result
    fixed = {e.room for e in rules.program.core}
    hosts = {id(r.host) for r in result if r.host is not None}
    movers = [
        r
        for r in rooms
        if (spec := rules.spec(r.type)).cluster
        and spec.stalls is None
        and r.type not in fixed
        and id(r) not in hosts
        and r.unit is None
        and r.host is None
        and not r.hallway
        and not r.hub
        and _box(r.cells) is not None
        and not r.cells & keep
    ]
    moved: int = 0
    for mover in sorted(movers, key=lambda r: -len(r.cells)):
        if moved >= MAX_SWAPS:
            break
        heir = _taker(mover.cells, result, mover, set(floor.circulation), rules)
        if heir is None:
            continue
        for pocket in _pockets(mover, floor, rules, keep)[:MAX_TRIES]:
            swaps = {id(mover): replace(mover, cells=pocket)}
            swaps[id(heir)] = replace(heir, cells=heir.cells | mover.cells)
            if _fits(result, swaps, floor, pocket, rules):
                result = _carve(result, swaps, pocket)
                floor = _Floor.of(result, rules)
                moved += 1
                break
    return result


def _piece(box: tuple[int, int, int, int], side: Side, floor: _Floor) -> frozenset[Cell]:
    """The corridor cells along the box's wall on `side`, across the corridor's whole depth,
    if the lobby (other circulation) lies on their far side; else empty."""
    piece: set[Cell] = set()
    depth = 0
    for start in beyond(box, side):
        cell, n = start, 0
        while cell in floor.corridor:
            piece.add(cell)
            cell = cell.neighbour(side)
            n += 1
        if n == 0 or cell not in floor.other or depth not in (0, n):
            return frozenset()
        depth = n
    return frozenset(piece)


def _pockets(
    mover: PlannedRoom, floor: _Floor, rules: Rules, keep: frozenset[Cell]
) -> list[frozenset[Cell]]:
    """Rectangles of corridor the room could take along the lobby, tucked ones (least open
    corridor) first."""
    spec = rules.spec(mover.type)
    top = min(spec.area[1], len(mover.cells))
    shapes = [
        (w, d)
        for w in range(spec.min_side, top // spec.min_side + 1)
        for d in range(spec.min_side, top // w + 1)
        if spec.area[0] <= w * d <= top and max(w, d) <= spec.max_aspect * min(w, d)
    ]
    # The room keeps its size where it can; a few shapes of it are enough.
    shapes = sorted(shapes, key=lambda wd: (-wd[0] * wd[1], abs(wd[0] - wd[1])))[:MAX_SHAPES]
    # Only rectangles that touch the lobby: a corner of each lies on a corridor cell next to it.
    border = [c for c in floor.corridor if any(c.neighbour(s) in floor.other for s in Side)]
    placed = {
        (b.x - cx, b.y - cy, w, d)
        for b in border
        for w, d in shapes
        for cx in (0, w - 1)
        for cy in (0, d - 1)
    }
    found: list[tuple[int, frozenset[Cell]]] = []
    for x0, y0, w, d in placed:
        x1, y1 = x0 + w - 1, y0 + d - 1
        if not all(Cell(x, y) in floor.corridor for x in (x0, x1) for y in (y0, y1)):
            continue
        cells = frozenset(Cell(x0 + i, y0 + j) for i in range(w) for j in range(d))
        if not cells <= floor.corridor or cells & keep:
            continue
        if door_run(cells, floor.other) < spec.door_width:
            continue
        open_edge = sum(
            1
            for c in cells
            for s in Side
            if (n := c.neighbour(s)) in floor.corridor and n not in cells
        )
        found.append((open_edge, cells))
    found.sort(key=lambda f: (f[0], -len(f[1]), min(f[1])))
    return [cells for _, cells in found]


def _fits(
    rooms: list[PlannedRoom],
    changed: dict[int, PlannedRoom],
    floor: _Floor,
    piece: frozenset[Cell],
    rules: Rules,
) -> bool:
    """Taking the piece out of the corridor keeps it wide enough, keeps circulation in one
    piece and leaves every room beside it a door's width of wall on circulation."""
    ring = {
        Cell(c.x + dx, c.y + dy) for c in piece for dx in (-1, 0, 1) for dy in (-1, 0, 1)
    } - piece
    # Circulation cells round the piece must join up round it (the piece cuts nothing off).
    around = ring & floor.circulation
    if around and len(_reach(around, around)) != len(around):
        return False
    corridor = floor.corridor - piece
    # The lobby is no way round: the corridor must stay as connected as it was.
    if len(_parts(corridor)) > len(_parts(floor.corridor)):
        return False
    near = frozenset(n for c in piece for s in Side if (n := c.neighbour(s)) in corridor)
    if near and thinnest(near, frozenset(corridor)) < rules.spec("corridor").min_side:
        return False
    circulation = set(floor.circulation - piece)
    for r in rooms:
        r = changed.get(id(r), r)
        spec = rules.spec(r.type)
        if spec.circulation or r.hub or r.hallway or r.unit is not None or r.host is not None:
            continue
        if r.cells & ring and door_run(r.cells, circulation) < spec.door_width:
            return False
    return True


def _reach(start: set[Cell], within: set[Cell]) -> set[Cell]:
    """The cells of `within` connected to one cell of `start`."""
    seen = {next(iter(start))}
    todo = list(seen)
    while todo:
        cell = todo.pop()
        for side in Side:
            if (n := cell.neighbour(side)) in within and n not in seen:
                seen.add(n)
                todo.append(n)
    return seen


def _carve(
    rooms: list[PlannedRoom], changed: dict[int, PlannedRoom], piece: frozenset[Cell]
) -> list[PlannedRoom]:
    """The rooms with `changed` swapped in and the piece cut out of the corridor."""
    result: list[PlannedRoom] = []
    for r in rooms:
        if id(r) in changed:
            result.append(changed[id(r)])
        elif r.type == "corridor" and not r.hallway and r.cells & piece:
            result += [replace(r, cells=part) for part in _parts(r.cells - piece)]
        else:
            result.append(r)
    return result


def _parts(cells: frozenset[Cell]) -> list[frozenset[Cell]]:
    """The connected pieces of a set of cells."""
    left = set(cells)
    parts: list[frozenset[Cell]] = []
    while left:
        part = _reach({next(iter(left))}, left)
        left -= part
        parts.append(frozenset(part))
    return parts
