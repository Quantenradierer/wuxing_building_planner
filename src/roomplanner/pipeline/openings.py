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
from collections.abc import Callable
from dataclasses import dataclass, replace

from roomplanner.geometry import Axis, Cell, Edge, Side, boundary_edges
from roomplanner.model import Floor, Opening, OpeningKind, Room, Swing
from roomplanner.params import EntranceKind
from roomplanner.pipeline.base import BuildingPlan, Context, EntranceRequest
from roomplanner.pipeline.registry import register
from roomplanner.rules import WindowRule

type Run = list[Edge]
# (rank, off the front, -length, room, first edge) and the wall run of a door candidate
type _Choice = tuple[tuple[int, bool, int, int, Edge], Run]


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
        core_walls = _core_walls(ctx, plan)
        for planned in plan.floors:
            rooms = tuple(
                Room(f"{planned.level}.{i + 1}", r.type, r.cells, r.unit)
                for i, r in enumerate(planned.rooms)
            )
            entries = {i for i, r in enumerate(planned.rooms) if r.entry}
            owner = {cell: i for i, room in enumerate(rooms) for cell in room.cells}
            circulation = {
                i
                for i, r in enumerate(rooms)
                if ctx.rules.spec(r.type).circulation or planned.rooms[i].hub
            }
            index = {id(r): i for i, r in enumerate(planned.rooms)}
            hosts = {
                i: index[id(r.host)]
                for i, r in enumerate(planned.rooms)
                if r.host is not None and id(r.host) in index
            }
            walls = _walls(footprint, owner, circulation)
            fixed = {
                i: core_walls[key]
                for i, r in enumerate(rooms)
                if (key := (r.type, min(r.cells))) in core_walls
            }
            fronts = {
                i: {Edge.of(c, r.front) for c in r.cells}
                for i, r in enumerate(planned.rooms)
                if r.front is not None
            }
            doors = _interior_doors(
                ctx, rooms, owner, walls, circulation, entries, hosts, rng, fixed, fronts
            )
            used: set[Edge] = set()
            grid = [w for w, _ in _window_grid(ctx, footprint, plan.facade_grid)]
            for request in planned.entrances:
                door = _exterior_door(ctx, footprint, rooms, request, used, grid, rng)
                if door is None:
                    plan.warnings.append(f"no facade for the {request.kind} entrance")
                    continue
                doors.append(door)
                used |= set(door.edges)
            if planned.level == 0:
                for room in rooms:
                    if ctx.rules.spec(room.type).exterior_door is None:
                        continue
                    door = _vehicle_door(ctx, footprint, room, doors, used, grid, rng)
                    if door is not None:
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


def _core_walls(ctx: Context, plan: BuildingPlan) -> dict[tuple[str, Cell], set[Edge]]:
    """Per core room (type, first cell): walls it shares with circulation on every floor."""
    core_types = {c.room for c in ctx.rules.program.core}
    common: dict[tuple[str, Cell], set[Edge]] = {}
    for planned in plan.floors:
        flow = {c for r in planned.rooms if ctx.rules.spec(r.type).circulation for c in r.cells}
        for room in planned.rooms:
            if room.type not in core_types:
                continue
            edges = {
                Edge.of(c, side) for c in room.cells for side in Side if c.neighbour(side) in flow
            }
            key = (room.type, min(room.cells))
            common[key] = common[key] & edges if key in common else edges
    return common


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
    fixed: dict[int, set[Edge]] | None = None,
    fronts: dict[int, set[Edge]] | None = None,
) -> list[Opening]:
    """One door per room, committed greedily: the best-ranked door of all pending rooms first.

    `fixed`: walls a room's door into circulation should use if it can (core rooms: the
    walls they share with circulation on every floor). `fronts`: the walls a room's door
    goes in if it can (stalls: towards the passage, never into a flank).

    Rank: into circulation, then into a type from the room's `access` list (in order), then
    anything else that allows transit; ties go to the longest shared wall. Rooms of a unit
    only connect within their unit, except its entry room, which opens to circulation.
    An annex (closet) opens only into its host and is never passed through.
    A room left without any allowed door (a storeroom behind the stairwell, walled in by
    apartments) finally opens into a core room.
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
        if rooms[i].type in core_types and rooms[j].type in core_types:
            return True  # the elevator may open into the stairwell wrapped around it
        if stranded and rooms[j].type in core_types:
            return True
        return ctx.rules.spec(rooms[j].type).transit

    stranded = False

    fixed = fixed or {}
    fronts = fronts or {}

    def best(i: int) -> _Choice | None:
        if i in fixed and (found := best_among(i, fixed[i])) is not None:
            return found
        return best_among(i, None)

    def best_among(i: int, only: set[Edge] | None) -> _Choice | None:
        spec = ctx.rules.spec(rooms[i].type)
        width = spec.door_width
        found: _Choice | None = None
        for j in neighbours[i]:
            if j not in connected or not allowed(i, j):
                continue
            if only is not None and j not in circulation:
                continue
            if j in circulation:
                rank = -1
            elif rooms[j].type in spec.access:
                rank = spec.access.index(rooms[j].type)
            else:
                rank = len(spec.access)
            runs = shared[(i, j)]
            if only is not None:
                runs = _runs({e for run in runs for e in run} & only)
            front = fronts.get(i, set())
            for run in runs:
                if len(run) >= width:
                    key = (rank, bool(front) and run[0] not in front, -len(run), i, run[0])
                    if found is None or key < found[0]:
                        found = (key, run)
        return found

    def partners(i: int) -> list[int]:
        """Rooms whose doors should be close to room i's (`next_to` / `connect`, both ways)."""
        spec = ctx.rules.spec(rooms[i].type)
        own = set(spec.next_to) | set(spec.connect)
        found: list[int] = []
        for k, room in enumerate(rooms):
            other = ctx.rules.spec(room.type)
            if k != i and (room.type in own or rooms[i].type in {*other.next_to, *other.connect}):
                found.append(k)
        return found

    def start_near(i: int, run: Run, width: int, margin: int) -> int:
        """Door position: close to the partners' doors (else their rooms), else by the wall's start.

        Core rooms (stairwell, elevator) share their cells on every floor: their door goes
        in the middle of the wall, so it is in the same place on all floors.
        """
        if rooms[i].type in core_types:
            return (len(run) - width) // 2
        others = partners(i)
        targets = [(e.x, e.y) for k in others for e in door_edges.get(k, [])]
        targets = targets or [(c.x, c.y) for k in others for c in rooms[k].cells]
        if not targets:
            # Always the same spot of the wall: rooms of one size get the same layout.
            return margin

        def gap(start: int) -> int:
            mid = run[start + width // 2]
            return min(abs(mid.x - x) + abs(mid.y - y) for x, y in targets)

        return min(range(margin, len(run) - width - margin + 1), key=gap)

    core_types = {c.room for c in ctx.rules.program.core}
    doors: list[Opening] = []
    door_edges: dict[int, list[Edge]] = defaultdict(list)
    linked: set[tuple[int, int]] = set()
    connected = set(circulation)
    pending = {i for i in range(len(rooms)) if i not in circulation}
    while pending:
        options = [(option, i) for i in sorted(pending) if (option := best(i)) is not None]
        if not options and not stranded:
            stranded = True
            continue
        if not options:
            break
        (_, run), i = min(options, key=lambda o: o[0][0])
        width = ctx.rules.spec(rooms[i].type).door_width
        # Keep doors off the corners; a narrow cubicle door may sit in one (room for the wc).
        margin = 1 if width > 1 and len(run) >= width + 2 else 0
        inside = next(c for c in run[0].cells() if owner.get(c) == i)
        outside = next(c for c in run[0].cells() if c != inside)
        start = start_near(i, run, width, margin)
        door = _door(run, width, inside, None, rng, start)
        doors.append(door)
        door_edges[i] += door.edges
        j = owner[outside]
        door_edges[j] += door.edges
        linked |= {(i, j), (j, i)}
        connected.add(i)
        pending.remove(i)
    # Direct doors between `connect` partners that share a wall (kitchen - restaurant).
    for (i, j), runs in sorted(shared.items()):
        spec = ctx.rules.spec(rooms[i].type)
        if rooms[j].type not in spec.connect or (i, j) in linked:
            continue
        if i in hosts or j in hosts or rooms[i].unit != rooms[j].unit:
            continue
        width = min(spec.door_width, ctx.rules.spec(rooms[j].type).door_width)
        fitting = [r for r in runs if len(r) >= width]
        if not fitting:
            continue
        run = max(fitting, key=len)
        inside = next(c for c in run[0].cells() if owner.get(c) == i)
        doors.append(_door(run, width, inside, None, rng, (len(run) - width) // 2))
        linked |= {(i, j), (j, i)}
    return doors


DOOR_WIDTH = 2  # a plain door, cells
ROOMY = 40  # cells (10 m²): smaller rooms get an exterior door only if nothing else can


def _exterior_door(
    ctx: Context,
    footprint: frozenset[Cell],
    rooms: tuple[Room, ...],
    request: EntranceRequest,
    used: set[Edge],
    windows: list[Opening],
    rng: random.Random,
) -> Opening | None:
    """The door in the requested room's facade; if that is too short, the nearest room's."""
    width = ctx.rules.entrances(ctx.params)[request.kind].width
    target = Edge.of(request.hint, request.side)
    hinted = rooms[request.room]
    # The program's service rooms in order of preference (loading bay before cold storage).
    service = [
        [r for r in rooms if r.unit is None and r.type == kind]
        for kind in ctx.rules.program.service_rooms
        if request.kind is EntranceKind.SERVICE
    ]
    others = [
        r
        for r in rooms
        if r is not hinted and r.unit is None and not ctx.rules.spec(r.type).circulation
    ]
    first = [hinted] if hinted.unit is None or request.kind is not EntranceKind.SERVICE else []
    last: list[Room] = []
    if request.kind is EntranceKind.SERVICE and hinted.type in {
        c.room for c in ctx.rules.program.core
    }:
        # Deliveries don't go through the stairwell (its stairs need that wall): only if
        # no other room on that facade can take the door.
        first, last = [], [hinted]

    def needs_window(room: Room) -> bool:
        return ctx.rules.spec(room.type).windows is WindowRule.REQUIRED

    # Tiny rooms (coffins, stalls) only as a last resort: the door would fill them.
    roomy = [r for r in first if r.area >= ROOMY], [r for r in others if r.area >= ROOMY]
    tiers = (*service, *roomy, first, others, last)
    # A wide door (loading dock) that would blind a room needing windows: a plain door.
    widths = [width, DOOR_WIDTH] if width > DOOR_WIDTH else [width]
    for candidates_from in tiers:
        for size in widths:
            found = _facade_spots(
                candidates_from, size, request.side, footprint, used, windows, target, needs_window
            )
            if not found:
                continue
            blind, _, _, run, start = min(found, key=lambda c: (c[0], c[1], c[2], c[3][0], c[4]))
            if blind and size != widths[-1]:
                continue
            door = _door(run, size, None, request.side, rng, start)
            return replace(door, entrance=request.kind.value)
    return None


def _vehicle_door(
    ctx: Context,
    footprint: frozenset[Cell],
    room: Room,
    doors: list[Opening],
    used: set[Edge],
    windows: list[Opening],
    rng: random.Random,
) -> Opening | None:
    """A room's own exterior door (`exterior_door`: loading bay, workshop), if it has none yet.

    On the service side if it can, the street side last; in the middle of the longest wall.
    None if the room has no facade that fits it.
    """
    width = ctx.rules.spec(room.type).exterior_door
    assert width is not None
    facade = {Edge.of(c, s) for c in room.cells for s in Side if c.neighbour(s) not in footprint}
    if not facade or any(len(d.edges) >= width and facade.issuperset(d.edges) for d in doors):
        return None  # the service entrance (a truck dock) already opens into it
    street, service = ctx.params.street_side, ctx.params.service_side
    anywhere = next(iter(facade))  # spots are ranked by their wall, not by a target
    for side in [service, *(s for s in Side if s not in (service, street)), street]:
        spots = _facade_spots(
            [room], width, side, footprint, used, windows, anywhere, lambda _: False
        )
        if spots:
            # The widest wall, then the middle of it.
            _, _, _, run, start = min(
                spots, key=lambda c: (-len(c[3]), abs(2 * c[4] + width - len(c[3])), c[4])
            )
            door = _door(run, width, None, side, rng, start)
            return replace(door, entrance=EntranceKind.SERVICE.value)
    return None


type Spot = tuple[bool, bool, int, Run, int]  # blind, tight, distance, run, start


def _facade_spots(
    rooms: list[Room],
    width: int,
    side: Side,
    footprint: frozenset[Cell],
    used: set[Edge],
    windows: list[Opening],
    target: Edge,
    needs_window: Callable[[Room], bool],
) -> list[Spot]:
    """Every place for an exterior door of `width` in these rooms' facades on `side`."""
    spots: list[Spot] = []
    for room in rooms:
        facade = {Edge.of(c, side) for c in room.cells if c.neighbour(side) not in footprint}
        own = [w for w in windows if facade.issuperset(w.edges)]
        for run in _runs(facade):
            if len(run) < width:
                continue
            margin = 1 if len(run) >= width + 2 else 0
            for start in range(0, len(run) - width + 1):
                edges = run[start : start + width]
                if used.intersection(edges):
                    continue
                centre = edges[len(edges) // 2]
                distance = abs(centre.x - target.x) + abs(centre.y - target.y)
                # A room needing windows must keep one beside the door.
                probe = Opening(OpeningKind.BREACH, tuple(edges))  # only its edges count
                blind = needs_window(room) and bool(own) and not _clear_of_doors(own, [probe])
                # Off the wall's ends unless that saves the room's window.
                tight = start < margin or start > len(run) - width - margin
                spots.append((blind, tight, distance, run, start))
    return spots


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
