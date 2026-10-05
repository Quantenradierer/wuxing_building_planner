"""Walls, doors and windows for planned rooms.

- Walls separate different rooms, except between two circulation rooms (corridor, lobby).
- Every other room gets one door, preferably into circulation, otherwise into a room that
  is already connected.
- Exterior doors are placed where the layout asked for them and open outwards.
- Windows sit on a facade grid shared by all floors above ground (so they line up); each
  floor omits the windows its own walls, doors or windowless rooms collide with. Rooms that
  need daylight then widen their windows (or get one off the grid) until they have a window
  cell per `DAYLIGHT` cells of floor.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace

from roomplanner.geometry import Axis, Cell, Diagonal, Edge, Side, boundary_edges
from roomplanner.model import Floor, Opening, OpeningKind, Room, Swing
from roomplanner.params import EntranceKind
from roomplanner.pipeline.base import BuildingPlan, Context, EntranceRequest
from roomplanner.pipeline.registry import register
from roomplanner.rules import WindowRule

type Run = list[Edge]
# Floor cells per window cell: MBO §47 asks for window area >= 1/8 of the floor, which a
# 1.5 m tall window meets at one 0.5 m window cell per 24 floor cells (6 m²).
DAYLIGHT = 24
# (rank, off the front, -length, room, first edge) and the wall run of a door candidate
type _Choice = tuple[tuple[int, bool, int, int, Edge], Run]


@dataclass
class _Draft:
    level: int
    role: str
    rooms: tuple[Room, ...]
    walls: frozenset[Edge]
    doors: list[Opening]
    footprint: frozenset[Cell]  # the building's, less what this floor lacks (under a balcony)
    diagonals: frozenset[Diagonal]


SHORT_DOOR = 0.8  # chance that an oblong core room's door goes in a short wall


@register("openings", "default")
class DefaultOpenings:
    def build(self, ctx: Context, building: frozenset[Cell], plan: BuildingPlan) -> list[Floor]:
        rng = ctx.rng("openings")
        building = building - plan.removed
        drafts: list[_Draft] = []
        core_walls = _core_walls(ctx, plan, rng)
        for planned in plan.floors:
            footprint = building - planned.cut
            rooms = tuple(
                Room(f"{planned.level}.{i + 1}", r.type, r.cells - plan.removed, r.unit)
                for i, r in enumerate(planned.rooms)
            )
            entries = {i for i, r in enumerate(planned.rooms) if r.entry}
            owner = {cell: i for i, room in enumerate(rooms) for cell in room.cells}
            circulation = {
                i
                for i, r in enumerate(rooms)
                if ctx.rules.spec(r.type).circulation or planned.rooms[i].hub
            }
            # Some rooms are open to the corridor now and then (open kitchens, vending rooms).
            opened = {
                i: _open_sides(rooms[i], owner, circulation)
                for i, r in enumerate(rooms)
                if (chance := ctx.rules.spec(r.type).open) > 0 and rng.random() < chance
            }
            index = {id(r): i for i, r in enumerate(planned.rooms)}
            hosts = {
                i: index[id(r.host)]
                for i, r in enumerate(planned.rooms)
                if r.host is not None and id(r.host) in index
            }
            # Circulation rooms that `connect` keep a wall with a door (narthex and nave).
            paired = {
                (i, j)
                for i, a in enumerate(rooms)
                for j, b in enumerate(rooms)
                if b.type in ctx.rules.spec(a.type).connect
            }
            walls = _walls(footprint, owner, circulation, opened, paired)
            circulation |= {i for i, sides in opened.items() if sides}
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
                ctx,
                rooms,
                owner,
                walls,
                circulation,
                entries,
                hosts,
                rng,
                fixed,
                fronts,
                {i for i, sides in opened.items() if sides},
                # Without circulation (a lone room) the street is it: the entered rooms
                # count as reached, so their stalls get doors.
                set() if circulation else {e.room for e in planned.entrances},
            )
            diagonals = frozenset(d for d in plan.diagonals if d.cell in footprint)
            cut = {e for d in diagonals for e in d.edges()}
            used: set[Edge] = set(cut)  # nothing opens in the outside of a diagonal
            grid = [w for w, _ in _window_grid(ctx, footprint, plan.facade_grid)]
            for request in planned.entrances:
                door = _exterior_door(ctx, footprint, rooms, request, used, grid, rng)
                if door is None:
                    plan.warnings.append(f"no facade for the {request.kind} entrance")
                    continue
                doors.append(door)
                used |= set(door.edges)
            if planned.level in (0, -1):  # -1: the garage's ramp up to the street
                for room in rooms:
                    door = _vehicle_door(ctx, footprint, room, doors, used, grid, rng)
                    if door is not None:
                        doors.append(door)
                        used |= set(door.edges)
            drafts.append(
                _Draft(planned.level, planned.role, rooms, walls, doors, footprint, diagonals)
            )

        candidates = _window_grid(ctx, building, plan.facade_grid)
        floors: list[Floor] = []
        for d in drafts:
            windows: list[Opening] = []
            if d.level >= 0:
                # Open-air rooms (a balcony): the windows look out onto them.
                outdoor = {c for r in d.rooms if ctx.rules.spec(r.type).outdoor for c in r.cells}
                indoor = d.footprint - outdoor
                same = indoor == building
                grid = candidates if same else _window_grid(ctx, indoor, plan.facade_grid)
                cut = {e for g in d.diagonals for e in g.edges()}
                fitting = [
                    w
                    for w, side in grid
                    if cut.isdisjoint(w.edges) and _window_fits(ctx, indoor, d, w, side)
                ]
                windows = _daylight(ctx, indoor, d, _clear_of_doors(fitting, d.doors))
            floors.append(
                Floor(
                    d.level,
                    d.footprint,
                    d.rooms,
                    d.walls,
                    tuple(d.doors + windows),
                    d.role,
                    diagonals=d.diagonals,
                )
            )
        return floors


def _core_walls(
    ctx: Context, plan: BuildingPlan, rng: random.Random
) -> dict[tuple[str, Cell], set[Edge]]:
    """Per core room (type, first cell): walls it shares with circulation on every floor.

    Most oblong core rooms keep only their short walls if they share one (`SHORT_DOOR`): a
    stairwell is usually entered through a short landing strip, not along a long one."""
    core_types = {c.room for c in ctx.rules.program.core}
    common: dict[tuple[str, Cell], set[Edge]] = {}
    boxes: dict[tuple[str, Cell], frozenset[Cell]] = {}
    for planned in plan.floors:
        flow = {
            c for r in planned.rooms if ctx.rules.spec(r.type).circulation or r.hub for c in r.cells
        }
        for room in planned.rooms:
            if room.type not in core_types:
                continue
            edges = {
                Edge.of(c, side) for c in room.cells for side in Side if c.neighbour(side) in flow
            }
            key = (room.type, min(room.cells))
            common[key] = common[key] & edges if key in common else edges
            boxes[key] = room.cells
    for key in sorted(common):
        cells = boxes[key]
        w = max(c.x for c in cells) - min(c.x for c in cells)
        h = max(c.y for c in cells) - min(c.y for c in cells)
        if w == h or rng.random() >= SHORT_DOOR:
            continue
        short = {e for e in common[key] if (e.axis is Axis.H) == (w < h)}
        if short:
            common[key] = short
    return common


def _open_sides(room: Room, owner: dict[Cell, int], circulation: set[int]) -> set[Side]:
    """The (at most two) sides an open room drops its walls on: those bordering the most
    circulation."""
    shared: dict[Side, int] = {}
    for cell in room.cells:
        for side in Side:
            if owner.get(cell.neighbour(side), -1) in circulation:
                shared[side] = shared.get(side, 0) + 1
    ranked = sorted(shared, key=lambda s: (-shared[s], list(Side).index(s)))
    return set(ranked[:2])


def _walls(
    footprint: frozenset[Cell],
    owner: dict[Cell, int],
    circulation: set[int],
    opened: dict[int, set[Side]] | None = None,
    paired: set[tuple[int, int]] | None = None,
) -> frozenset[Edge]:
    """Walls between different rooms, except between circulation rooms (unless `paired`:
    they connect by a door) and on the open sides of `opened` rooms towards circulation."""
    opened = opened or {}
    paired = paired or set()
    walls = set(boundary_edges(footprint))
    for cell in footprint:
        for side in (Side.E, Side.S):
            neighbour = cell.neighbour(side)
            if neighbour not in footprint:
                continue
            # Cells a (faulty) layout left without a room are walled off; the validator
            # reports them instead of this stage crashing.
            a, b = owner.get(cell), owner.get(neighbour)
            if a == b:
                continue
            both = a in circulation and b in circulation
            if both and (a, b) not in paired and (b, a) not in paired:
                continue
            if a in opened and b in circulation and side in opened[a]:
                continue
            if b in opened and a in circulation and side.opposite in opened[b]:
                continue
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
    opened: set[int] | None = None,
    entered: set[int] | None = None,
) -> list[Opening]:
    """One door per room, committed greedily: the best-ranked door of all pending rooms first.

    `fixed`: walls a room's door into circulation should use if it can (core rooms: the
    walls they share with circulation on every floor). `fronts`: the walls a room's door
    goes in if it can (stalls: towards the passage, never into a flank).

    Rank: into circulation (one in the room's `access` list first), then into a type from
    the `access` list (in order), then
    anything else that allows transit; ties go to the longest shared wall. Rooms of a unit
    only connect within their unit, except its entry room, which opens to circulation.
    An annex (closet) opens only into its host and is never passed through.
    A room left without any allowed door (a storeroom behind the core between rooms that
    are no thoroughfares, walled in by apartments) finally opens into any neighbour, a core
    room last, in the program's core order (the stairwell before an elevator car). Open
    rooms (`opened`: an open kitchen) need no door and take other rooms' doors only as that
    last resort. `entered`: rooms with an exterior door, reached from the start like
    circulation (a lone room, entered from the street).
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
        if j in opened_rooms:
            return stranded
        if j in circulation:
            return unit_i is None or i in entries
        if unit_i != unit_j:
            return False
        if rooms[i].type in core_types and rooms[j].type in core_types:
            return True  # the elevator may open into the stairwell wrapped around it
        if stranded:
            return True  # through a room that is not meant for it, a core room last
        return ctx.rules.spec(rooms[j].type).transit

    stranded = False
    opened_rooms = opened or set()

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
                rank = -2 if rooms[j].type in spec.access else -1
            elif rooms[j].type in core_types:
                # Stairs before an elevator: a door onto a landing, not into the car.
                rank = len(spec.access) + 1 + core_types.index(rooms[j].type)
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

    def start_near(i: int, j: int, run: Run, width: int, margin: int) -> int:
        """Door position: close to the partners' doors (else their rooms), else by the wall's start.

        Core rooms (stairwell, elevator) share their cells on every floor: their door goes
        in the middle of the wall, so it is in the same place on all floors. A stranded room
        opening into a core room goes close to the core's own door (the landing).
        """
        if rooms[i].type in core_types:
            return (len(run) - width) // 2
        others = [j] if rooms[j].type in core_types else partners(i)
        targets = [(e.x, e.y) for k in others for e in door_edges.get(k, [])]
        targets = targets or [(c.x, c.y) for k in others for c in rooms[k].cells]
        if not targets:
            # Always the same spot of the wall: rooms of one size get the same layout.
            return margin

        def gap(start: int) -> int:
            mid = run[start + width // 2]
            return min(abs(mid.x - x) + abs(mid.y - y) for x, y in targets)

        return min(range(margin, len(run) - width - margin + 1), key=gap)

    core_types = list(dict.fromkeys(c.room for c in ctx.rules.program.core))
    doors: list[Opening] = []
    door_edges: dict[int, list[Edge]] = defaultdict(list)
    linked: set[tuple[int, int]] = set()
    connected = set(circulation) | (entered or set())
    # Entered rooms are reached from outside: they need no door of their own.
    pending = {i for i in range(len(rooms)) if i not in connected}
    while pending:
        options = [(option, i) for i in sorted(pending) if (option := best(i)) is not None]
        if not options and not stranded:
            stranded = True
            continue
        if not options:
            break
        (_, run), i = min(options, key=lambda o: o[0][0])
        spec = ctx.rules.spec(rooms[i].type)
        width = spec.door_width
        # Keep doors off the corners; a narrow cubicle door may sit in one (room for the wc).
        margin = 1 if width > 1 and len(run) >= width + 2 else 0
        inside = next(c for c in run[0].cells() if owner.get(c) == i)
        outside = next(c for c in run[0].cells() if c != inside)
        start = start_near(i, owner[outside], run, width, margin)
        # A room laid out in advance has its door where its template has it (or mirrored).
        spots = [
            s
            for t in spec.templates
            if t.size[0] == len(run)
            for s in (t.door, len(run) - t.door - width)
        ]
        if spots:
            start = min(spots, key=lambda s: (abs(s - start), s))
        door = _door(
            run, width, outside if spec.opens_out(rooms[i].area) else inside, None, rng, start
        )
        if spec.door_slides:
            door = replace(door, sliding=True)
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
    back = request.kind is EntranceKind.SERVICE
    # The service door is the vehicle door of a bay if there is one, else a back room's
    # door (the program's service rooms in order: loading bay before cold storage) or the
    # corridor's end.
    vehicle = [r for r in rooms if back and ctx.rules.spec(r.type).facade_door]
    service = [
        [r for r in rooms if r.unit is None and r.type == kind]
        for kind in ctx.rules.program.service_rooms
        if back
    ]
    corridor = [r for r in rooms if back and ctx.rules.spec(r.type).circulation]
    others = [
        r
        for r in rooms
        if r is not hinted and r.unit is None and not ctx.rules.spec(r.type).circulation
    ]
    first = [hinted] if hinted.unit is None or request.kind is not EntranceKind.SERVICE else []
    last: list[Room] = []
    core = {c.room for c in ctx.rules.program.core}
    if request.kind is EntranceKind.SERVICE and hinted.type in core:
        # Deliveries don't go through the stairwell (its stairs need that wall): only if
        # no other room on that facade can take the door.
        first, last = [], [hinted]

    def needs_window(room: Room) -> bool:
        return ctx.rules.spec(room.type).windows is WindowRule.REQUIRED

    # Tiny rooms (coffins, stalls) only as a last resort: the door would fill them. A small
    # hall is still a hall: the requested lobby keeps the door (a flat's entry hall).
    def is_roomy(room: Room) -> bool:
        return room.area >= ROOMY or ctx.rules.spec(room.type).circulation

    roomy = [r for r in first if is_roomy(r)], [r for r in others if r.area >= ROOMY]
    # The layout reserved a back room or corridor stub for it (`service_stub`): that first.
    reserved = [hinted] if back and ctx.rules.program.service_stub else []
    tiers = (vehicle, reserved, *service, corridor, *roomy, first, others, last)
    # A wide door (loading dock) that would blind a room needing windows: a plain door.
    widths = [width, DOOR_WIDTH] if width > DOOR_WIDTH else [width]
    if request.kind is EntranceKind.EMERGENCY and hinted.type in core:
        widths = [min(width, DOOR_WIDTH)]  # a stairwell's exit: on its landing, between stairs
    for candidates_from in tiers:
        # A bay gets its own wide door anyway; elsewhere the back door is a plain one.
        for size in widths if candidates_from is vehicle or not vehicle else [DOOR_WIDTH]:
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
    """A bay's own exterior door (`facade_door`) if it has none yet: service side first."""
    width = ctx.rules.spec(room.type).facade_door
    if width is None:
        return None
    outside = {e for e in boundary_edges(room.cells) if not footprint.issuperset(e.cells())}
    if any(len(d.edges) >= width and outside.issuperset(d.edges) for d in doors):
        return None  # a door this wide (a truck dock) already opens into it
    service, street = ctx.params.service_side, ctx.params.street_side
    sides = [service, *(s for s in Side if s not in (service, street)), street]
    centre = sorted(room.cells)[len(room.cells) // 2]

    def needs_window(r: Room) -> bool:
        return ctx.rules.spec(r.type).windows is WindowRule.REQUIRED

    for side in sides:
        found = _facade_spots(
            [room], width, side, footprint, used, windows, Edge.of(centre, side), needs_window
        )
        # A roller door that would blind the bay (its facade is barely wider): no door.
        found = [c for c in found if not c[0]]
        if found:
            _, _, _, run, start = min(found, key=lambda c: (c[1], c[2], c[3][0], c[4]))
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
                # Off the wall's ends unless that saves the room's window or the hint is
                # there (a stairwell's exit on its landing).
                tight = target not in edges and (
                    start < margin or start > len(run) - width - margin
                )
                if needs_window(room):
                    # Daylight: a door at the wall's end costs one margin, not two.
                    tight = start not in (0, len(run) - width)
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


def _before(edge: Edge) -> Edge:
    """The preceding edge on the same line (west for H, north for V)."""
    if edge.axis is Axis.H:
        return Edge(edge.x - 1, edge.y, edge.axis)
    return Edge(edge.x, edge.y - 1, edge.axis)


def _door_margins(doors: list[Opening]) -> set[Edge]:
    """The doors' edges and one edge of wall on either side of each."""
    return {e for d in doors for e in (*d.edges, _before(d.edges[0]), d.edges[-1].next_along())}


def _clear_of_doors(windows: list[Opening], doors: list[Opening]) -> list[Opening]:
    """Windows not touching this floor's doors (with one edge of wall in between)."""
    blocked = _door_margins(doors)
    return [w for w in windows if blocked.isdisjoint(w.edges)]


def _daylight(
    ctx: Context, footprint: frozenset[Cell], draft: _Draft, windows: list[Opening]
) -> list[Opening]:
    """Widen the windows of rooms needing daylight until they have a window cell per
    `DAYLIGHT` floor cells, a few edges at a time round their windows; a room without a
    window, or whose windows cannot grow, gets one in its longest free stretch of facade.
    Windows keep an edge of wall between each other and beside doors."""
    result = list(windows)
    blocked = _door_margins(draft.doors)
    taken = {e for w in result for e in w.edges} | {e for g in draft.diagonals for e in g.edges()}

    def free(edge: Edge, facade: dict[Edge, Side]) -> bool:
        return edge in facade and edge not in blocked and edge not in taken

    for room in draft.rooms:
        if ctx.rules.spec(room.type).windows is not WindowRule.REQUIRED:
            continue
        facade = {
            Edge.of(c, side): side
            for c in room.cells
            for side in Side
            if c.neighbour(side) not in footprint
        }
        own = [i for i, w in enumerate(result) if w.edges[0] in facade]
        short = -(-room.area // DAYLIGHT) - sum(len(result[i].edges) for i in own)
        while short > 0:
            grown = False
            for i in own:
                edges = result[i].edges
                side = facade[edges[0]]
                # Alternate ends so the window stays centred on its module.
                ends = [(edges[-1].next_along(), True), (_before(edges[0]), False)]
                if len(edges) % 2:
                    ends.reverse()
                for edge, after in ends:
                    beyond = edge.next_along() if after else _before(edge)
                    joint = (edge.x, edge.y) if after else (edges[0].x, edges[0].y)
                    if (
                        free(edge, facade)
                        and beyond not in taken
                        and _inward(joint, side) not in draft.walls
                    ):
                        grown_edges = (*edges, edge) if after else (edge, *edges)
                        result[i] = replace(result[i], edges=grown_edges)
                        taken.add(edge)
                        short -= 1
                        grown = True
                        break
                if short <= 0:
                    break
            if grown:
                continue
            # A new window in the longest stretch clear of doors and other windows.
            spots = [
                e
                for e in facade
                if free(e, facade) and _before(e) not in taken and e.next_along() not in taken
            ]
            runs = [
                run
                for run in _runs(set(spots))
                if not any(_inward((e.x, e.y), facade[e]) in draft.walls for e in run[1:])
            ]
            if not runs:
                break
            run = max(runs, key=len)
            width = min(len(run), short, ctx.rules.program.facade.window)
            start = (len(run) - width) // 2
            result.append(Opening(OpeningKind.WINDOW, tuple(run[start : start + width])))
            taken |= set(run[start : start + width])
            own.append(len(result) - 1)
            short -= width
    return result


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
