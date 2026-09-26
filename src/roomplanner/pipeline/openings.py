"""Walls, doors and windows for planned rooms.

- Walls separate different rooms, except between two circulation rooms (corridor, lobby).
- Every other room gets one door, preferably into circulation, otherwise into a room that
  is already connected.
- Exterior doors are placed where the layout asked for them and open outwards.
- Windows sit on a facade grid shared by all floors above ground (so they line up); each
  floor omits the windows its own walls, doors or windowless rooms collide with.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, replace

from roomplanner.geometry import Axis, Cell, Edge, Side, boundary_edges
from roomplanner.model import Floor, Opening, OpeningKind, Room, Swing
from roomplanner.pipeline.base import BuildingPlan, Context, EntranceRequest
from roomplanner.pipeline.registry import register
from roomplanner.rules import WindowRule

type Run = list[Edge]


@dataclass
class _Draft:
    level: int
    role: str
    rooms: tuple[Room, ...]
    walls: frozenset[Edge]
    doors: list[Opening]


@register("openings", "default")
class DefaultOpenings:
    def build(self, ctx: Context, footprint: frozenset[Cell], plan: BuildingPlan) -> list[Floor]:
        rng = ctx.rng("openings")
        drafts: list[_Draft] = []
        for planned in plan.floors:
            rooms = tuple(
                Room(f"{planned.level}.{i + 1}", r.type, r.cells, r.unit)
                for i, r in enumerate(planned.rooms)
            )
            entries = {i for i, r in enumerate(planned.rooms) if r.entry}
            owner = {cell: i for i, room in enumerate(rooms) for cell in room.cells}
            circulation = {i for i, r in enumerate(rooms) if ctx.rules.spec(r.type).circulation}
            index = {id(r): i for i, r in enumerate(planned.rooms)}
            hosts = {
                i: index[id(r.host)]
                for i, r in enumerate(planned.rooms)
                if r.host is not None and id(r.host) in index
            }
            walls = _walls(footprint, owner, circulation)
            doors = _interior_doors(ctx, rooms, owner, walls, circulation, entries, hosts, rng)
            used: set[Edge] = set()
            for request in planned.entrances:
                door = _exterior_door(ctx, footprint, rooms[request.room], request, used, rng)
                if door is None:
                    plan.warnings.append(f"no facade for the {request.kind} entrance")
                    continue
                doors.append(door)
                used |= set(door.edges)
            drafts.append(_Draft(planned.level, planned.role, rooms, walls, doors))

        candidates = _window_grid(ctx, footprint, plan.facade_grid)
        floors: list[Floor] = []
        for d in drafts:
            windows: list[Opening] = []
            if d.level >= 0:
                fitting = [w for w, side in candidates if _window_fits(ctx, footprint, d, w, side)]
                windows = _clear_of_doors(fitting, d.doors)
            floors.append(
                Floor(d.level, footprint, d.rooms, d.walls, tuple(d.doors + windows), d.role)
            )
        return floors


def _walls(
    footprint: frozenset[Cell], owner: dict[Cell, int], circulation: set[int]
) -> frozenset[Edge]:
    walls = set(boundary_edges(footprint))
    for cell in footprint:
        for side in (Side.E, Side.S):
            neighbour = cell.neighbour(side)
            if neighbour not in footprint:
                continue
            # Cells a (faulty) layout left without a room are walled off; the validator
            # reports them instead of this stage crashing.
            a, b = owner.get(cell), owner.get(neighbour)
            if a != b and not (a in circulation and b in circulation):
                walls.add(Edge.of(cell, side))
    return frozenset(walls)


def _runs(edges: set[Edge]) -> list[Run]:
    """Split edges into straight, contiguous runs."""
    runs: list[Run] = []
    for axis in Axis:
        # Order along the line: horizontal runs share y, vertical runs share x.
        line = sorted(
            (e for e in edges if e.axis is axis),
            key=lambda e: (e.y, e.x) if axis is Axis.H else (e.x, e.y),
        )
        for edge in line:
            if runs and runs[-1][-1].next_along() == edge:
                runs[-1].append(edge)
            else:
                runs.append([edge])
    return runs


def _door(
    run: Run, width: int, into: Cell | None, outwards: Side | None, rng: random.Random, start: int
) -> Opening:
    edges = tuple(run[start : start + width])
    axis = run[0].axis
    if outwards is not None:
        towards = outwards
    else:
        assert into is not None
        first, _ = edges[0].cells()
        if axis is Axis.H:
            towards = Side.N if first.y == into.y else Side.S
        else:
            towards = Side.W if first.x == into.x else Side.E
    hinges = (Side.W, Side.E) if axis is Axis.H else (Side.N, Side.S)
    return Opening(OpeningKind.DOOR, edges, Swing(towards, rng.choice(hinges)))


def _interior_doors(
    ctx: Context,
    rooms: tuple[Room, ...],
    owner: dict[Cell, int],
    walls: frozenset[Edge],
    circulation: set[int],
    entries: set[int],
    hosts: dict[int, int],
    rng: random.Random,
) -> list[Opening]:
    """One door per room, committed greedily: the best-ranked door of all pending rooms first.

    Rank: into circulation, then into a type from the room's `access` list (in order), then
    anything else that allows transit; ties go to the longest shared wall. Rooms of a unit
    only connect within their unit, except its entry room, which opens to circulation.
    An annex (closet) opens only into its host and is never passed through.
    """
    shared: dict[tuple[int, int], list[Run]] = {}
    pairs: dict[tuple[int, int], set[Edge]] = defaultdict(set)
    for edge in walls:
        a, b = edge.cells()
        if a in owner and b in owner:
            i, j = owner[a], owner[b]
            pairs[(i, j)].add(edge)
            pairs[(j, i)].add(edge)
    neighbours: dict[int, list[int]] = defaultdict(list)
    for (i, j), edges in pairs.items():
        shared[(i, j)] = _runs(edges)
        neighbours[i].append(j)

    def allowed(i: int, j: int) -> bool:
        if i in hosts:
            return j == hosts[i]
        if j in hosts:
            return False
        unit_i, unit_j = rooms[i].unit, rooms[j].unit
        if j in circulation:
            return unit_i is None or i in entries
        if unit_i != unit_j:
            return False
        return ctx.rules.spec(rooms[j].type).transit

    def best(i: int) -> tuple[tuple[int, int, int, Edge], Run] | None:
        spec = ctx.rules.spec(rooms[i].type)
        width = spec.door_width
        found: tuple[tuple[int, int, int, Edge], Run] | None = None
        for j in neighbours[i]:
            if j not in connected or not allowed(i, j):
                continue
            if j in circulation:
                rank = -1
            elif rooms[j].type in spec.access:
                rank = spec.access.index(rooms[j].type)
            else:
                rank = len(spec.access)
            for run in shared[(i, j)]:
                if len(run) >= width:
                    key = (rank, -len(run), i, run[0])
                    if found is None or key < found[0]:
                        found = (key, run)
        return found

    doors: list[Opening] = []
    connected = set(circulation)
    pending = {i for i in range(len(rooms)) if i not in circulation}
    while pending:
        options = [(option, i) for i in sorted(pending) if (option := best(i)) is not None]
        if not options:
            break
        (_, run), i = min(options, key=lambda o: o[0][0])
        width = ctx.rules.spec(rooms[i].type).door_width
        margin = 1 if len(run) >= width + 2 else 0
        start = rng.randint(margin, len(run) - width - margin)
        inside = next(c for c in run[0].cells() if owner.get(c) == i)
        doors.append(_door(run, width, inside, None, rng, start))
        connected.add(i)
        pending.remove(i)
    return doors


def _exterior_door(
    ctx: Context,
    footprint: frozenset[Cell],
    room: Room,
    request: EntranceRequest,
    used: set[Edge],
    rng: random.Random,
) -> Opening | None:
    width = ctx.rules.entrances(ctx.params)[request.kind].width
    facade = {
        Edge.of(c, request.side) for c in room.cells if c.neighbour(request.side) not in footprint
    }
    target = Edge.of(request.hint, request.side)
    candidates: list[tuple[int, Run, int]] = []
    for run in _runs(facade):
        if len(run) < width:
            continue
        margin = 1 if len(run) >= width + 2 else 0
        for start in range(margin, len(run) - width - margin + 1):
            edges = run[start : start + width]
            if used.intersection(edges):
                continue
            centre = edges[len(edges) // 2]
            distance = abs(centre.x - target.x) + abs(centre.y - target.y)
            candidates.append((distance, run, start))
    if not candidates:
        return None
    _, run, start = min(candidates, key=lambda c: (c[0], c[1][0], c[2]))
    return replace(_door(run, width, None, request.side, rng, start), entrance=request.kind.value)


def _inward(vertex: tuple[int, int], side: Side) -> Edge:
    """The edge running from a facade vertex into the building."""
    x, y = vertex
    match side:
        case Side.N:
            return Edge(x, y, Axis.V)
        case Side.S:
            return Edge(x, y - 1, Axis.V)
        case Side.W:
            return Edge(x, y, Axis.H)
        case Side.E:
            return Edge(x - 1, y, Axis.H)


def _window_grid(
    ctx: Context, footprint: frozenset[Cell], grids: dict[Axis, tuple[int, int]]
) -> list[tuple[Opening, Side]]:
    """Every window position of the facade grid, shared by all floors above ground."""
    program = ctx.rules.program
    module = program.facade.module
    width = min(module, program.facade.window)
    windows: list[tuple[Opening, Side]] = []
    for side in Side:
        facade = {Edge.of(c, side) for c in footprint if c.neighbour(side) not in footprint}
        for run in _runs(facade):
            axis = run[0].axis
            if axis in grids and grids[axis][1] == module:
                # Align with the layout's partition grid (absolute coordinates).
                start_coord = run[0].x if axis is Axis.H else run[0].y
                offset = (grids[axis][0] - start_coord) % module
            else:
                offset = (len(run) % module) // 2
            for start in range(offset, len(run) - module + 1, module):
                edges = run[start + (module - width) // 2 :][:width]
                windows.append((Opening(OpeningKind.WINDOW, tuple(edges)), side))
    return windows


def _clear_of_doors(windows: list[Opening], doors: list[Opening]) -> list[Opening]:
    """Windows not touching this floor's doors (with one edge of wall in between)."""
    blocked: set[Edge] = set()
    for door in doors:
        first, last = door.edges[0], door.edges[-1]
        before = (
            Edge(first.x - 1, first.y, first.axis)
            if first.axis is Axis.H
            else Edge(first.x, first.y - 1, first.axis)
        )
        blocked |= {*door.edges, before, last.next_along()}
    return [w for w in windows if blocked.isdisjoint(w.edges)]


def _window_fits(
    ctx: Context, footprint: frozenset[Cell], draft: _Draft, window: Opening, side: Side
) -> bool:
    """False if a wall of this floor runs into the window or the room must stay windowless."""
    edges = window.edges
    # A partition may meet the window's ends, but not run into its middle.
    vertices = [(e.x, e.y) for e in edges[1:]]
    if any(_inward(v, side) in draft.walls for v in vertices):
        return False
    for edge in edges:
        inside = next(c for c in edge.cells() if c in footprint)
        room = next((r for r in draft.rooms if inside in r.cells), None)
        if room is None or ctx.rules.spec(room.type).windows is WindowRule.FORBIDDEN:
            return False
    return True
