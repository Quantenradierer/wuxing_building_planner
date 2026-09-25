"""Algorithm-agnostic invariant checks. See docs/architecture.md, section "Data model"."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum

from roomplanner.geometry import Cell, Edge, Side, boundary_edges
from roomplanner.model import Building, Floor, OpeningKind


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


def validate(building: Building) -> list[Violation]:
    violations: list[Violation] = []
    for floor in building.floors:
        violations += _check_floor(building, floor)
    return violations


def hard_violations(building: Building) -> list[Violation]:
    return [v for v in validate(building) if v.severity is Severity.HARD]


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
    doors = {e for o in floor.openings if o.kind is OpeningKind.DOOR for e in o.edges}
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
