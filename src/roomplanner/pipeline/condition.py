"""Condition layer: wear and damage, from `data/condition.yaml` by the `condition` level.

In this order: furniture is removed, doors are broken, torn out or blocked, windows
shattered, walls breached, debris and collapsed spots scattered, lights switched to
flicker or off. The building stays reachable: a door is only blocked if nothing gets cut
off, exterior doors are never blocked, breaches only add connections and debris goes
through the furnisher's walkability checks.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import replace

from roomplanner.geometry import Axis, Cell, Edge, Side
from roomplanner.layer_rules import ConditionTable, ConditionTier, load_condition
from roomplanner.model import Floor, Light, Opening, OpeningKind, OpeningState, PlacedObject
from roomplanner.pipeline.base import Context
from roomplanner.pipeline.furnishing import RoomFurnisher, solid_walls
from roomplanner.pipeline.registry import register
from roomplanner.rules import FurnitureRule, Placement

BREACH_WIDTH = 2  # edges
MAX_DEBRIS = 40  # per room: a roof is as big as the floor, and every piece costs a room scan
MIN_BREACH_WALL = 4  # a wall run must be this long to take a breach


@register("condition", "rules")
class RulesCondition:
    def apply(self, ctx: Context, floors: list[Floor]) -> list[Floor]:
        table = load_condition()
        tier = table.tiers[ctx.params.condition]
        worn: list[Floor] = []
        for floor in floors:
            rng = ctx.rng(f"condition:{floor.level}")
            floor = _remove_furniture(floor, tier, table, rng)
            floor = replace(floor, openings=_damage_openings(floor, tier, rng))
            floor = _breaches(floor, tier, rng)
            floor = _debris(ctx, floor, tier, table, rng)
            floor = replace(floor, lights=tuple(_light(li, tier, rng) for li in floor.lights))
            worn.append(floor)
        return worn


def _remove_furniture(
    floor: Floor, tier: ConditionTier, table: ConditionTable, rng: random.Random
) -> Floor:
    """Remove a share of the furniture, but never free a cell walled in by other objects."""
    if tier.furniture_removed <= 0:
        return floor
    keep = set(table.fixtures)
    rooms = {room.id: room for room in floor.rooms}
    blocking: dict[str, set[Cell]] = {}
    for obj in floor.objects:
        if obj.blocking:
            blocking.setdefault(obj.room, set()).update(obj.cells)
    removed: set[int] = set()
    for i, obj in enumerate(floor.objects):
        if obj.kind in keep or not obj.blocking or rng.random() >= tier.furniture_removed:
            continue
        others = blocking[obj.room] - obj.cells
        free = rooms[obj.room].cells - others
        # The freed cells join the (connected) free floor iff one of them touches it.
        touching = (c.neighbour(side) for c in obj.cells for side in Side)
        if any(n in free and n not in obj.cells for n in touching):
            blocking[obj.room] = others
            removed.add(i)
    objects = tuple(o for i, o in enumerate(floor.objects) if i not in removed)
    return replace(floor, objects=objects)


def _damage_openings(floor: Floor, tier: ConditionTier, rng: random.Random) -> tuple[Opening, ...]:
    openings = list(floor.openings)
    for i, opening in enumerate(openings):
        roll = rng.random()
        if opening.kind is OpeningKind.WINDOW:
            if roll < tier.windows_broken:
                openings[i] = replace(opening, state=OpeningState.BROKEN)
            continue
        if opening.kind is not OpeningKind.DOOR:
            continue
        if roll < tier.doors_broken:
            openings[i] = replace(opening, state=OpeningState.BROKEN)
        elif roll < tier.doors_broken + tier.doors_missing:
            openings[i] = replace(opening, state=OpeningState.MISSING)
        elif roll < tier.doors_broken + tier.doors_missing + tier.doors_blocked:
            exterior = opening.entrance is not None or floor.is_exterior_wall(opening.edges[0])
            blocked = replace(opening, state=OpeningState.BLOCKED)
            trial = replace(floor, openings=(*openings[:i], blocked, *openings[i + 1 :]))
            if not exterior and _all_reachable(trial):
                openings[i] = blocked
    return tuple(openings)


def _all_reachable(floor: Floor) -> bool:
    """Every cell reachable from the exterior doors (ground floor) or from any cell."""
    passable = floor.passable_edges()
    starts = {
        c for e in passable if floor.is_exterior_wall(e) for c in e.cells() if c in floor.footprint
    }
    if floor.level != 0 or not starts:
        starts = {min(floor.footprint)}
    seen = set(starts)
    queue = deque(starts)
    while queue:
        cell = queue.popleft()
        for side in Side:
            neighbour = cell.neighbour(side)
            if neighbour in seen or neighbour not in floor.footprint:
                continue
            edge = Edge.of(cell, side)
            if edge in floor.walls and edge not in passable:
                continue
            seen.add(neighbour)
            queue.append(neighbour)
    return len(seen) == len(floor.footprint)


def _breaches(floor: Floor, tier: ConditionTier, rng: random.Random) -> Floor:
    count = _count(tier.breaches * len(floor.footprint) / 1000, rng)
    if count == 0:
        return floor
    owner = {cell: room.id for room in floor.rooms for cell in room.cells}
    taken = {e for o in floor.openings for e in o.edges}
    # Keep a wall edge between openings, and keep breaches off the ends of runs.
    near = {n for e in taken for n in _along(e, 2)}
    runs: list[list[Edge]] = []
    for edge in sorted(floor.walls, key=_line_order):
        a, b = edge.cells()
        if floor.is_exterior_wall(edge) or edge in near:
            continue
        if owner.get(a) is None or owner.get(b) is None or owner[a] == owner[b]:
            continue
        runs.append([edge])
    candidates = _straight_runs(runs, owner)
    rng.shuffle(candidates)
    breaches: list[Opening] = []
    used: set[Edge] = set()
    for run in candidates:
        if len(breaches) == count:
            break
        if len(run) < MIN_BREACH_WALL or used.intersection(run):
            continue
        start = rng.randint(1, len(run) - BREACH_WIDTH - 1)
        edges = tuple(run[start : start + BREACH_WIDTH])
        breaches.append(Opening(OpeningKind.BREACH, edges))
        used |= {n for e in run for n in _along(e, 2)}
    return replace(floor, openings=(*floor.openings, *breaches))


def _line_order(edge: Edge) -> tuple[Axis, int, int]:
    """Sort key putting edges of one straight line next to each other."""
    return (edge.axis, edge.y, edge.x) if edge.axis is Axis.H else (edge.axis, edge.x, edge.y)


def _along(edge: Edge, reach: int) -> list[Edge]:
    """The edge and its neighbours on the same line."""
    if edge.axis is Axis.H:
        return [Edge(edge.x + d, edge.y, edge.axis) for d in range(-reach, reach + 1)]
    return [Edge(edge.x, edge.y + d, edge.axis) for d in range(-reach, reach + 1)]


def _straight_runs(pieces: list[list[Edge]], owner: dict[Cell, str]) -> list[list[Edge]]:
    """Join single wall edges into runs between the same two rooms."""
    runs: list[list[Edge]] = []
    for (edge,) in pieces:
        if runs:
            last = runs[-1][-1]
            if last.next_along() == edge and _pair(last, owner) == _pair(edge, owner):
                runs[-1].append(edge)
                continue
        runs.append([edge])
    return runs


def _pair(edge: Edge, owner: dict[Cell, str]) -> tuple[str, str]:
    a, b = edge.cells()
    return owner[a], owner[b]


def _debris(
    ctx: Context, floor: Floor, tier: ConditionTier, table: ConditionTable, rng: random.Random
) -> Floor:
    if tier.debris_per is None and tier.collapses <= 0:
        return floor
    clearances = floor.door_clearances()
    solid = solid_walls(floor)
    objects: list[PlacedObject] = list(floor.objects)
    collapses = _count(tier.collapses * len(floor.footprint) / 1000, rng)
    collapse_rooms = rng.sample(list(floor.rooms), min(collapses, len(floor.rooms)))
    for room in floor.rooms:
        rules: list[FurnitureRule] = []
        if room in collapse_rooms:
            kind = rng.choice(table.collapse)
            rules.append(FurnitureRule(object=kind, placement=Placement.SCATTER))
        if tier.debris_per is not None:
            debris = table.debris + (table.rubble if tier.rubble else [])
            count = min(_count(room.area / tier.debris_per, rng), MAX_DEBRIS)
            for _ in range(count):
                kind = rng.choice(debris)
                rules.append(FurnitureRule(object=kind, placement=Placement.SCATTER))
        if not rules:
            continue
        existing = [o for o in objects if o.room == room.id]
        clearance = clearances.get(room.id, frozenset())
        furnisher = RoomFurnisher(ctx, floor, room, rng, clearance, solid, existing)
        objects += furnisher.place(rules, scale=False)
    return replace(floor, objects=tuple(objects))


def _count(expected: float, rng: random.Random) -> int:
    """Whole number with the given expectation."""
    whole = math.floor(expected)
    return whole + (1 if rng.random() < expected - whole else 0)


def _light(light: Light, tier: ConditionTier, rng: random.Random) -> Light:
    roll = rng.random()
    if not tier.power and light.kind != "emergency":
        return replace(light, state="off")
    if roll < tier.lights_off:
        return replace(light, state="off")
    if roll < tier.lights_off + tier.lights_flicker:
        return replace(light, state="flicker")
    return light
