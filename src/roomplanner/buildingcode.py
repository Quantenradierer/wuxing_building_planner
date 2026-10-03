"""Soft building-code checks (German MBO, ASR) on a generated building.

Walking distances use octile moves (diagonals through open corners); furniture is ignored.
Exits: exterior doors on the ground floor, stairwells (and public stairs) elsewhere; roof
floors are skipped. `tools/codecheck.py` measures the same rules over a sample of buildings.
"""

from __future__ import annotations

import heapq
import math
from collections import defaultdict

from roomplanner.geometry import Cell, Edge, Side
from roomplanner.model import Building, Floor, OpeningKind, Room
from roomplanner.params import Condition
from roomplanner.rules import Rules

STAIRS = frozenset({"stairwell", "public_stairs"})
WORK_DESKS = frozenset({"desk", "office_desk", "executive_desk", "console_desk"})
ESCAPE_CELLS = 70  # 35 m, MBO §35
DEAD_END_CELLS = 30  # 15 m
RESCUE_LEVELS = range(1, 8)  # floors whose rooms can use a window as the second route
SIDES = list(Side)
UNCHECKED = frozenset({Condition.DERELICT, Condition.RUINED})


class Grid:
    def __init__(self, floor: Floor) -> None:
        self.floor = floor
        self.cells = floor.footprint
        self.walls = floor.walls
        self.passable = floor.passable_edges()
        self.owner = {c: r for r in floor.rooms for c in r.cells}

    def open(self, a: Cell, b: Cell) -> bool:
        if b not in self.cells:
            return False
        e = Edge.between(a, b)
        return e not in self.walls or e in self.passable

    def neighbours(self, c: Cell):
        for s in SIDES:
            n = c.neighbour(s)
            if self.open(c, n):
                yield n, 1.0
        for dx, dy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            h, v, d = Cell(c.x + dx, c.y), Cell(c.x, c.y + dy), Cell(c.x + dx, c.y + dy)
            if self.open(c, h) and self.open(h, d) and self.open(c, v) and self.open(v, d):
                yield d, math.sqrt(2)

    def dist(self, sources: set[Cell]) -> dict[Cell, float]:
        best = {s: 0.0 for s in sources}
        heap = [(0.0, s) for s in sources]
        while heap:
            d, c = heapq.heappop(heap)
            if d > best[c]:
                continue
            for n, w in self.neighbours(c):
                nd = d + w
                if nd < best.get(n, math.inf):
                    best[n] = nd
                    heapq.heappush(heap, (nd, n))
        return best


def exits(floor: Floor) -> list[set[Cell]]:
    """Ground floor: each exterior door (the cells inside it). Other floors: each stairwell."""
    if floor.level == 0:
        out: list[set[Cell]] = []
        for o in floor.openings:
            if o.kind is OpeningKind.DOOR and o.passable and floor.is_exterior_wall(o.edges[0]):
                out.append({c for e in o.edges for c in e.cells() if c in floor.footprint})
        return out
    return [set(r.cells) for r in floor.rooms if r.type in STAIRS]


def code_warnings(building: Building, rules: Rules) -> list[str]:
    """One message per floor and rule; empty for derelict and ruined buildings."""
    if building.params.condition in UNCHECKED:
        return []
    out: list[str] = []
    for floor in building.floors:
        if any(r.type == "roof" for r in floor.rooms):
            continue
        out += [f"building code, level {floor.level}: {m}" for m in _check_floor(floor, rules)]
    return out


def _window_cells(floor: Floor) -> dict[Cell, int]:
    count: dict[Cell, int] = defaultdict(int)
    for o in floor.openings:
        if o.kind is OpeningKind.WINDOW:
            for e in o.edges:
                for c in e.cells():
                    count[c] += 1
    return count


def _check_floor(floor: Floor, rules: Rules) -> list[str]:
    messages: list[str] = []
    grid = Grid(floor)
    ex = exits(floor)
    dists = [grid.dist(e) for e in ex]
    exit_cells = {c for e in ex for c in e}
    skip = {c for r in floor.rooms if r.type in STAIRS | {"elevator"} for c in r.cells}

    far = [
        c
        for c in floor.footprint - skip - exit_cells
        if min((d.get(c, math.inf) for d in dists), default=math.inf) > ESCAPE_CELLS
    ]
    if far:
        worst = max(far, key=lambda c: min((d.get(c, math.inf) for d in dists), default=math.inf))
        room = grid.owner.get(worst)
        where = f"{room.type} {room.id}" if room else str(worst)
        messages.append(f"{len(far)} cells farther than 35 m from an exit, e.g. {where}")

    # Two routes: habitable rooms that reach fewer than two exits and have no rescue window.
    windows = _window_cells(floor)
    single: list[Room] = []
    for room in floor.rooms:
        if room.type in STAIRS | {"elevator"} or room.type not in rules.rooms:
            continue
        if rules.spec(room.type).windows.value != "required":
            continue
        cell = next(iter(room.cells))
        if sum(1 for d in dists if cell in d) < 2 and not (
            floor.level in RESCUE_LEVELS and not windows.keys().isdisjoint(room.cells)
        ):
            single.append(room)
    if single:
        messages.append(
            f"{len(single)} rooms with one escape route and no rescue window, "
            f"e.g. {single[0].type} {single[0].id}"
        )

    dead = _dead_end(floor, grid, ex, dists)
    if dead is not None:
        messages.append(f"dead-end corridor of {dead * 0.5:.1f} m (limit 15 m)")

    thin: list[Room] = []
    for room in floor.rooms:
        if room.type not in rules.rooms or rules.spec(room.type).windows.value != "required":
            continue
        n = sum(windows.get(c, 0) for c in room.cells)
        # A window cell is 0.5 m wide and 1.5 m tall: 1/8 of the floor area (MBO §47).
        if n * 0.5 * 1.5 < room.area * 0.25 / 8:
            thin.append(room)
    if thin:
        messages.append(
            f"{len(thin)} rooms with under 1/8 window area, e.g. {thin[0].type} {thin[0].id}"
        )

    desks: dict[str, int] = defaultdict(int)
    for o in floor.objects:
        if o.kind in WORK_DESKS:
            desks[o.room] += 1
    tight = [
        r for r in floor.rooms if (n := desks.get(r.id, 0)) and r.area * 0.25 < 8 + 6 * (n - 1)
    ]
    if tight:
        messages.append(
            f"{len(tight)} rooms under 8 m² + 6 m² per further workstation, "
            f"e.g. {tight[0].type} {tight[0].id}"
        )
    return messages


def _dead_end(
    floor: Floor, grid: Grid, ex: list[set[Cell]], dists: list[dict[Cell, float]]
) -> float | None:
    """The longest dead-end stretch (cells) of a corridor if over the limit, else None."""
    pair = [
        [min((dists[j].get(c, math.inf) for c in ex[i]), default=math.inf) for j in range(len(ex))]
        for i in range(len(ex))
    ]
    longest = 0.0
    for room in floor.rooms:
        if room.type != "corridor":
            continue
        for c in room.cells:
            ds = [d.get(c, math.inf) for d in dists]
            if len(ex) >= 2:
                length = min(
                    (ds[i] + ds[j] - pair[i][j]) / 2
                    for i in range(len(ex))
                    for j in range(i + 1, len(ex))
                )
            else:
                length = ds[0] if ds else math.inf
            longest = max(longest, length)
    return longest if longest > DEAD_END_CELLS else None
