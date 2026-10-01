"""Algorithm-agnostic invariant checks. See docs/architecture.md, section "Data model"."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from roomplanner.geometry import (
    Cell,
    Edge,
    Side,
    boundary_edges,
    connected,
    largest_rectangle,
)
from roomplanner.model import Building, Floor, OpeningKind
from roomplanner.rules import Rules, WindowRule


class Severity(StrEnum):
    HARD = "hard"
    SOFT = "soft"


@dataclass(frozen=True, slots=True)
class Violation:
    severity: Severity
    level: int
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] level {self.level}: {self.message}"


def validate(building: Building, rules: Rules | None = None) -> list[Violation]:
    """Structural checks always; rule checks (room sizes, core, windows) when rules are given."""
    violations: list[Violation] = []
    for floor in building.floors:
        violations += _check_floor(building, floor)
        violations += _check_objects(floor)
        if rules is not None:
            violations += _check_rules(floor, rules)
    if rules is not None:
        violations += _check_core(building, rules)
    return violations


def hard_violations(building: Building, rules: Rules | None = None) -> list[Violation]:
    return [v for v in validate(building, rules) if v.severity is Severity.HARD]


def _check_floor(building: Building, floor: Floor) -> list[Violation]:
    problems: list[str] = []

    outside = [
        c for c in floor.footprint if not (0 <= c.x < building.width and 0 <= c.y < building.height)
    ]
    if outside:
        problems.append(f"{len(outside)} footprint cells outside the map, e.g. {min(outside)}")

    problems += _check_rooms(floor)
    problems += _check_walls(floor)
    problems += _check_openings(floor)
    problems += _check_connectivity(floor)

    return [Violation(Severity.HARD, floor.level, p) for p in problems]


def _check_rooms(floor: Floor) -> list[str]:
    problems: list[str] = []
    ids = [room.id for room in floor.rooms]
    if len(ids) != len(set(ids)):
        problems.append("duplicate room ids")
    covered: set[Cell] = set()
    for room in floor.rooms:
        if not room.cells:
            problems.append(f"room {room.id} is empty")
        if overlap := covered & room.cells:
            problems.append(f"room {room.id} overlaps other rooms at {min(overlap)}")
        covered |= room.cells
    if covered - floor.footprint:
        problems.append("rooms extend beyond the footprint")
    if uncovered := floor.footprint - covered:
        problems.append(
            f"{len(uncovered)} footprint cells belong to no room, e.g. {min(uncovered)}"
        )
    return problems


def _check_walls(floor: Floor) -> list[str]:
    problems: list[str] = []
    if missing := boundary_edges(floor.footprint) - floor.walls:
        problems.append(f"exterior wall missing at {min(missing)}")
    stray = [e for e in floor.walls if not any(c in floor.footprint for c in e.cells())]
    if stray:
        problems.append(f"wall not touching the footprint at {min(stray)}")
    return problems


def _check_openings(floor: Floor) -> list[str]:
    problems: list[str] = []
    used: set[Edge] = set()
    for opening in floor.openings:
        edges = set(opening.edges)
        if not edges <= floor.walls:
            problems.append(f"{opening.kind} at {opening.edges[0]} is not on a wall")
        if edges & used:
            problems.append(f"{opening.kind} at {opening.edges[0]} overlaps another opening")
        used |= edges
    if floor.level == 0 and not any(
        o.kind is OpeningKind.DOOR and floor.is_exterior_wall(o.edges[0]) for o in floor.openings
    ):
        problems.append("ground floor has no exterior door")
    return problems


def _check_connectivity(floor: Floor) -> list[str]:
    """Every footprint cell must be reachable without crossing walls.

    Ground floor: reachable from the exterior doors. Other floors: connected to the largest
    region (until milestone 2 adds stairs as the entry point).
    """
    if not floor.footprint:
        return ["floor has no footprint"]
    doors = set(floor.passable_edges())
    entrances = {
        cell
        for edge in doors
        if floor.is_exterior_wall(edge)
        for cell in edge.cells()
        if cell in floor.footprint
    }
    if floor.level == 0 and entrances:
        reachable = _flood(floor, doors, entrances)
    else:
        remaining = set(floor.footprint)
        reachable: frozenset[Cell] = frozenset()
        while remaining:
            region = _flood(floor, doors, {min(remaining)})
            remaining -= region
            reachable = max(reachable, region, key=len)
    if unreachable := floor.footprint - reachable:
        return [f"{len(unreachable)} cells unreachable, e.g. {min(unreachable)}"]
    return []


def _flood(floor: Floor, doors: set[Edge], start: set[Cell]) -> frozenset[Cell]:
    seen = set(start)
    queue = deque(start)
    while queue:
        cell = queue.popleft()
        for side in Side:
            neighbour = cell.neighbour(side)
            if neighbour in seen or neighbour not in floor.footprint:
                continue
            edge = Edge.of(cell, side)
            if edge in floor.walls and edge not in doors:
                continue
            seen.add(neighbour)
            queue.append(neighbour)
    return frozenset(seen)


def _check_rules(floor: Floor, rules: Rules) -> list[Violation]:
    violations: list[Violation] = []
    window_cells = {
        c
        for o in floor.openings
        if o.kind is OpeningKind.WINDOW
        for e in o.edges
        for c in e.cells()
    }
    # Circulation rooms have no walls between them: their width counts as one open space.
    open_space = frozenset(
        c
        for r in floor.rooms
        if r.type in rules.rooms and rules.spec(r.type).circulation
        for c in r.cells
    )
    for room in floor.rooms:
        if room.type not in rules.rooms:
            violations.append(
                Violation(Severity.HARD, floor.level, f"unknown room type {room.type}")
            )
            continue
        spec = rules.spec(room.type)
        space = open_space if spec.circulation else room.cells
        thinnest = _thinnest(room.cells, space)
        if thinnest < spec.min_side and not (
            not spec.circulation and _alcoved(room.cells, spec.min_side)
        ):
            message = (
                f"{room.type} {room.id} is {thinnest} cells wide in places, minimum {spec.min_side}"
            )
            violations.append(Violation(Severity.HARD, floor.level, message))
        has_window = not window_cells.isdisjoint(room.cells)
        if spec.windows is WindowRule.REQUIRED and floor.level >= 0 and not has_window:
            message = f"{room.type} {room.id} has no window"
            violations.append(Violation(Severity.SOFT, floor.level, message))
    return violations


MIN_ALCOVE = 3  # cells (1.5 m): the least width of a part of a room beyond its main rectangle


def _alcoved(cells: frozenset[Cell], min_side: int) -> bool:
    """An irregular room: its largest rectangle is at least `min_side` each way and every
    part beyond it (an alcove, a wing) at least MIN_ALCOVE wide."""
    x0, y0, x1, y1 = largest_rectangle(cells)
    if min(x1 - x0, y1 - y0) < min_side:
        return False
    rest = frozenset(c for c in cells if not (x0 <= c.x < x1 and y0 <= c.y < y1))
    return not rest or _thinnest(rest, cells) >= min(MIN_ALCOVE, min_side)


def _thinnest(cells: frozenset[Cell], space: frozenset[Cell]) -> int:
    """Smallest extent of `space` through any of `cells`, horizontally or vertically."""
    horizontal = _run_lengths(space, lambda c: (c.y, c.x))
    vertical = _run_lengths(space, lambda c: (c.x, c.y))
    return min(min(horizontal[c], vertical[c]) for c in cells)


def _run_lengths(cells: frozenset[Cell], key: Callable[[Cell], tuple[int, int]]) -> dict[Cell, int]:
    """Length of the straight run through each cell; `key` gives (line, position on line)."""
    lengths: dict[Cell, int] = {}
    run: list[Cell] = []
    for cell in sorted(cells, key=key):
        if run and key(run[-1]) != (key(cell)[0], key(cell)[1] - 1):
            lengths.update(dict.fromkeys(run, len(run)))
            run = []
        run.append(cell)
    lengths.update(dict.fromkeys(run, len(run)))
    return lengths


def _check_core(building: Building, rules: Rules) -> list[Violation]:
    """Core rooms (stairs, elevators) sit at the same cells on every floor, and their doors
    into circulation at the same edges (the stairs don't move between floors)."""
    violations: list[Violation] = []
    for entry in rules.active_core(building.params):
        placements = {
            floor.level: frozenset(r.cells for r in floor.rooms if r.type == entry.room)
            for floor in building.floors
        }
        reference = placements[building.floors[0].level]
        doors = {
            floor.level: _circulation_doors(floor, entry.room, rules) for floor in building.floors
        }
        first_doors = doors[building.floors[0].level]
        for level, cells in placements.items():
            if not cells:
                violations.append(Violation(Severity.HARD, level, f"{entry.room} missing"))
            elif cells != reference:
                violations.append(Violation(Severity.HARD, level, f"{entry.room} not aligned"))
            elif doors[level] != first_doors:
                violations.append(Violation(Severity.HARD, level, f"{entry.room} door moved"))
    return violations


def _circulation_doors(floor: Floor, room_type: str, rules: Rules) -> frozenset[Edge]:
    """Door edges between rooms of `room_type` and circulation rooms on one floor."""
    owner = {cell: room for room in floor.rooms for cell in room.cells}
    found: set[Edge] = set()
    for opening in floor.openings:
        if opening.kind is not OpeningKind.DOOR:
            continue
        for edge in opening.edges:
            rooms = [owner.get(c) for c in edge.cells()]
            if None in rooms:
                continue
            types = {r.type for r in rooms if r is not None}
            others = types - {room_type}
            if room_type in types and others and all(rules.spec(t).circulation for t in others):
                found.add(edge)
    return frozenset(found)


def _check_objects(floor: Floor) -> list[Violation]:
    """Objects stay inside their room, don't overlap, keep doors clear and rooms walkable."""
    problems: list[str] = []
    rooms = {room.id: room for room in floor.rooms}
    clearances = floor.door_clearances()
    taken: dict[str, set[Cell]] = {}
    blocking: dict[str, set[Cell]] = {}
    for obj in floor.objects:
        room = rooms.get(obj.room)
        if room is None or not obj.cells <= room.cells:
            problems.append(f"{obj.kind} at ({obj.x}, {obj.y}) is outside room {obj.room}")
            continue
        cells = taken.setdefault(obj.room, set())
        if cells & obj.cells:
            problems.append(f"{obj.kind} at ({obj.x}, {obj.y}) overlaps another object")
        cells |= obj.cells
        if obj.blocking:
            blocking.setdefault(obj.room, set()).update(obj.cells)
    # Objects that can be walked over (stairs) may stand in front of doors.
    for room_id, cells in blocking.items():
        room = rooms[room_id]
        if cells & clearances.get(room_id, frozenset()):
            problems.append(f"objects block a door of {room.type} {room_id}")
        if not _reachable(floor, room.cells, room.cells - cells):
            problems.append(f"objects cut {room.type} {room_id} into unreachable parts")
    return [Violation(Severity.HARD, floor.level, p) for p in problems]


def _reachable(floor: Floor, room: frozenset[Cell], free: frozenset[Cell]) -> bool:
    """The room's free floor is one piece, or every piece touches a side without a wall (an
    open office along the corridor): reachable from the neighbouring room."""
    if connected(free):
        return True
    open_cells = {
        c
        for c in room
        for s in Side
        if (n := c.neighbour(s)) not in room
        and n in floor.footprint
        and Edge.of(c, s) not in floor.walls
    }
    left = set(free)
    while left:
        start = left.pop()
        piece, stack = {start}, [start]
        while stack:
            cell = stack.pop()
            for side in Side:
                n = cell.neighbour(side)
                if n in left:
                    left.remove(n)
                    piece.add(n)
                    stack.append(n)
        if not piece & open_cells:
            return False
    return True
