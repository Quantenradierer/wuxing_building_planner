"""Partition layout: the floor is one big room that gets cut into smaller ones.

1. The lobby is reserved at the street facade (ground floor), then a corridor spine is cut
   out along the long axis of the footprint, joined to the lobby or the street, and
   branched wherever some cell would be farther from circulation than a row of rooms is
   deep.
2. The core (stairwells, lifts) takes chunks next to the corridor.
3. What is left splits into regions. Per floor role the regions are typed and cut further
   (`partition_assign.py`): the rooms the floor must have first, then the role's mix.

Corridor and core are the same on every floor; floors of one role share their partition.
Any footprint works: the footprint is simply the big room. See docs/architecture.md.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace

from roomplanner.geometry import Cell, Side, thinnest_extent
from roomplanner.params import EntranceKind
from roomplanner.pipeline.base import (
    AllocationError,
    BuildingPlan,
    Context,
    EntranceRequest,
    FloorPlan,
    PlannedRoom,
)
from roomplanner.pipeline.layout.corridor import FAR_CORE_MIN
from roomplanner.pipeline.layout.leftovers import absorb_leftovers
from roomplanner.pipeline.layout.partition_assign import Assigner
from roomplanner.pipeline.layout.regions import (
    bbox,
    components,
    contact,
    descend,
    distances,
    facade,
    opened,
    thicken,
)
from roomplanner.pipeline.layout.stalls import carve_stalls
from roomplanner.pipeline.registry import register
from roomplanner.rules import Priority, RoomEntry, Rules, evaluate, variables

CORE_CANDIDATES = 12  # core positions scored per room
SLIVER = 16  # cells: a free piece smaller than this beside the core counts as waste


@dataclass
class Skeleton:
    corridor: frozenset[Cell]
    cores: list[PlannedRoom]
    lobby: frozenset[Cell]  # empty without a lobby
    lobby_type: str | None
    main_hint: Cell  # facade cell of the main entrance
    main_side: Side
    far: frozenset[Cell] = frozenset()  # cells of the second stairwell, which has an exit


@register("layout", "partition")
class PartitionLayout:
    def main_min_depth(self, ctx: Context) -> int:
        return ctx.rules.program.corridor.width + ctx.rules.program.strip_depth[0]

    def check_feasibility(self, ctx: Context, footprint: frozenset[Cell]) -> list[str]:
        program = ctx.rules.program
        if program.units:
            return [f"{program.building}: units are not supported by the partition layout yet"]
        problems: list[str] = []
        if min(ctx.width, ctx.height) < self.main_min_depth(ctx):
            problems.append(
                f"building is {min(ctx.width, ctx.height)} cells across, needs at least "
                f"{self.main_min_depth(ctx)} for its corridor and rooms"
            )
        core = sum(c.size[0] * c.size[1] for c in ctx.rules.active_core(ctx.params))
        corridor = program.corridor.width * max(ctx.width, ctx.height)
        for level in ctx.params.levels:
            _, role = ctx.rules.role_for(level, ctx.params)
            needed = core + corridor
            for entry in role.rooms:
                if entry.priority is Priority.REQUIRED and evaluate(
                    entry.when, variables(ctx.params, level)
                ):
                    low = (entry.area or ctx.rules.spec(entry.room).area)[0]
                    needed += low * entry.count_range[0]
            if needed > len(footprint):
                problems.append(
                    f"level {level}: core, corridor and required rooms need {needed} cells, "
                    f"the floor has {len(footprint)}"
                )
        return problems

    def layout(self, ctx: Context, footprint: frozenset[Cell]) -> BuildingPlan:
        rng = ctx.rng("layout")
        skeleton = self._skeleton(ctx, footprint, rng)
        plan = BuildingPlan(floors=[])
        shared: dict[tuple[str, bool], tuple[list[PlannedRoom], list[str]]] = {}
        for level in ctx.params.levels:
            plan.floors.append(self._floor(ctx, footprint, skeleton, level, shared, plan))
        return plan

    # --- skeleton ---------------------------------------------------------------------

    def _skeleton(self, ctx: Context, footprint: frozenset[Cell], rng: random.Random) -> Skeleton:
        program = ctx.rules.program
        width = program.corridor.width
        street = ctx.params.street_side
        lobby_entry = _lobby_entry(ctx)
        corridor = self._bands(ctx, footprint, rng)
        corridor = self._connect(footprint, corridor, width)
        corridor = self._branch(footprint, corridor, frozenset(), frozenset(), ctx)
        corridor = self._tidy(corridor, frozenset(), width)
        corridor = self._back_corridor(ctx, footprint, corridor, width)
        lobby = frozenset[Cell]()
        hint: Cell | None = None
        if lobby_entry is not None:
            found = self._lobby_beside(ctx, footprint, corridor, street, lobby_entry, rng)
            if found is None:
                found = self._lobby(ctx, footprint, street, lobby_entry, rng)
                if found is None:
                    raise AllocationError("no space for the lobby")
                corridor = self._join(footprint, corridor, found[0], width, rng)
            lobby, hint = found
        else:
            hint, path = self._gate(footprint, corridor, street, width)
            corridor |= path
        corridor = self._tidy(corridor, lobby, width)
        cores, far = self._cores(ctx, footprint, corridor | lobby, lobby, rng)
        return Skeleton(
            corridor, cores, lobby, lobby_entry.room if lobby_entry else None, hint, street, far
        )

    def _lobby(
        self,
        ctx: Context,
        footprint: frozenset[Cell],
        street: Side,
        entry: RoomEntry,
        rng: random.Random,
    ) -> tuple[frozenset[Cell], Cell] | None:
        """A rectangle against the street facade, in its longest straight stretch."""
        spec = ctx.rules.spec(entry.room)
        low, high = entry.area or spec.area
        area = rng.randint(low, high)
        along = (1, 0) if street in (Side.N, Side.S) else (0, 1)
        inward = street.opposite.delta
        runs: dict[int, list[int]] = {}
        for cell, _ in facade(footprint, footprint):
            if cell.neighbour(street) not in footprint:
                key, pos = (cell.y, cell.x) if along == (1, 0) else (cell.x, cell.y)
                runs.setdefault(key, []).append(pos)
        stretches: list[tuple[int, int, int]] = []  # (length, key, first position)
        for key, positions in runs.items():
            positions.sort()
            start = previous = positions[0]
            for pos in [*positions[1:], None]:
                if pos is None or pos != previous + 1:
                    stretches.append((previous - start + 1, key, start))
                    if pos is not None:
                        start = pos
                if pos is not None:
                    previous = pos
        if not stretches:
            return None
        length, key, first = max(stretches)
        side = max(spec.min_side, round(math.sqrt(area * 1.6)))
        wide = min(length, side)
        start = first + (length - wide) // 2
        start += rng.randint(-((length - wide) // 3), (length - wide) // 3)
        for depth in range(max(spec.min_side, math.ceil(area / wide)), spec.min_side - 1, -1):
            cells: set[Cell] = set()
            for i in range(wide):
                for j in range(depth):
                    pos = start + i
                    x = pos * along[0] + key * (1 - along[0]) + inward[0] * j
                    y = pos * along[1] + key * (1 - along[1]) + inward[1] * j
                    cells.add(Cell(x, y))
            if cells <= footprint:
                pos = start + wide // 2
                at = Cell(
                    pos * along[0] + key * (1 - along[0]), pos * along[1] + key * (1 - along[1])
                )
                return frozenset(cells), at
        return None

    @staticmethod
    def _bands(ctx: Context, footprint: frozenset[Cell], rng: random.Random) -> frozenset[Cell]:
        """Parallel corridors along the long axis, as many as keep each row of rooms between
        them (and the facades) within the program's strip depth, joined by cross corridors."""
        program = ctx.rules.program
        width = program.corridor.width
        smin, smax = program.strip_depth
        x0, y0, x1, y1 = bbox(footprint)
        horizontal = x1 - x0 >= y1 - y0
        lo, hi = (y0, y1) if horizontal else (x0, x1)
        ulo, uhi = (x0, x1) if horizontal else (y0, y1)
        count = 1
        while count * width + (count + 1) * smax < hi - lo:
            count += 1
        while count > 1 and (hi - lo - count * width) / (count + 1) < smin:
            count -= 1
        rows = (hi - lo - count * width) / (count + 1)

        def cross(c: Cell) -> int:
            return c.y if horizontal else c.x

        def along(c: Cell) -> int:
            return c.x if horizontal else c.y

        starts: list[int] = []
        for i in range(count):
            shift = rng.randint(-1, 1) if rows >= smin + 2 else 0
            starts.append(lo + round(rows * (i + 1) + width * i) + shift)
        cells = {c for c in footprint if any(t <= cross(c) < t + width for t in starts)}
        if count > 1:
            length = uhi - ulo
            ends = [ulo + round(rows), uhi - round(rows) - width]
            spots = ends if length >= 4 * rows else [(ulo + uhi - width) // 2]
            span = (starts[0], starts[-1] + width)
            cells |= {
                c
                for c in footprint
                if span[0] <= cross(c) < span[1] and any(u <= along(c) < u + width for u in spots)
            }
        return frozenset(cells)

    @staticmethod
    def _connect(
        footprint: frozenset[Cell], corridor: frozenset[Cell], width: int
    ) -> frozenset[Cell]:
        """Join the pieces a non-rectangular footprint cuts the corridors into."""
        for _ in range(8):
            pieces = components(corridor)
            if len(pieces) < 2:
                break
            main = pieces[0]
            dist = distances(main, footprint)
            piece = pieces[1]
            start = min(piece, key=lambda c: (dist.get(c, 10**9), c))
            if start not in dist:
                break
            corridor |= thicken(descend(start, dist), width, footprint)
        return corridor

    @staticmethod
    def _lobby_beside(
        ctx: Context,
        footprint: frozenset[Cell],
        corridor: frozenset[Cell],
        street: Side,
        entry: RoomEntry,
        rng: random.Random,
    ) -> tuple[frozenset[Cell], Cell] | None:
        """A lobby on the street facade whose inner side lies on a corridor: as deep as the
        row of rooms there, as wide as its area asks (centred on the facade if it can)."""
        spec = ctx.rules.spec(entry.room)
        low, high = entry.area or spec.area
        along = (1, 0) if street in (Side.N, Side.S) else (0, 1)
        inward = street.opposite.delta
        edge = [c for c in footprint if c.neighbour(street) not in footprint]
        if not edge:
            return None
        positions = sorted({c.x if along == (1, 0) else c.y for c in edge})
        middle = (positions[0] + positions[-1]) / 2
        best: tuple[float, frozenset[Cell], Cell] | None = None
        for first in positions[::2]:
            base = next(c for c in edge if (c.x if along == (1, 0) else c.y) == first)
            for wide in range(spec.min_side, 31, 2):
                # the depth at which the rectangle meets a corridor, measured mid-way
                mid = first + wide // 2
                col = [c for c in edge if (c.x if along == (1, 0) else c.y) == mid]
                if not col:
                    continue
                depth = 0
                cell = col[0]
                while cell in footprint and cell not in corridor:
                    depth += 1
                    cell = Cell(cell.x + inward[0], cell.y + inward[1])
                if cell not in corridor or not spec.min_side <= depth <= 24:
                    continue
                if not low <= wide * depth <= high * 1.2:
                    continue
                rect = frozenset(
                    Cell(
                        base.x + i * along[0] + j * inward[0],
                        base.y + i * along[1] + j * inward[1],
                    )
                    for i in range(wide)
                    for j in range(depth)
                )
                if not rect <= footprint or not rect.isdisjoint(corridor):
                    continue
                if contact(rect, corridor) < min(wide, 4):
                    continue
                score = abs(mid - middle) + rng.uniform(0, 2)
                if best is None or score < best[0]:
                    hint = col[0]
                    best = (score, rect, hint)
        if best is None:
            return None
        return best[1], best[2]

    @staticmethod
    def _join(
        footprint: frozenset[Cell],
        corridor: frozenset[Cell],
        lobby: frozenset[Cell],
        width: int,
        rng: random.Random,
    ) -> frozenset[Cell]:
        """Corridor from the spine to the lobby, unless they already touch."""
        if contact(lobby, corridor) >= 2:
            return corridor - lobby
        corridor -= lobby
        dist = distances(corridor, footprint - lobby)
        borders = [
            c for c in footprint - lobby if c in dist and any(c.neighbour(s) in lobby for s in Side)
        ]
        if not borders:
            return corridor
        start = min(borders, key=lambda c: (dist[c], c))
        path = descend(start, dist)
        return corridor | (thicken([start, *path], width, footprint) - lobby)

    @staticmethod
    def _gate(
        footprint: frozenset[Cell], corridor: frozenset[Cell], street: Side, width: int
    ) -> tuple[Cell, frozenset[Cell]]:
        """Without a lobby: the street facade cell near the middle that the corridor reaches
        with the shortest branch (none needed if the corridor itself touches the street)."""
        x0, y0, x1, y1 = bbox(footprint)
        middle = ((x0 + x1) / 2, (y0 + y1) / 2)
        dist = distances(corridor, footprint)
        edge = [c for c in footprint if c.neighbour(street) not in footprint]
        gate = min(
            edge,
            key=lambda c: (dist[c] + 0.3 * (abs(c.x - middle[0]) + abs(c.y - middle[1])), c),
        )
        path = descend(gate, dist) if dist[gate] > 0 else []
        return gate, thicken([gate, *path], width, footprint) if path else frozenset()

    @staticmethod
    def _branch(
        footprint: frozenset[Cell],
        corridor: frozenset[Cell],
        lobby: frozenset[Cell],
        access: frozenset[Cell],
        ctx: Context,
    ) -> frozenset[Cell]:
        """Side corridors toward cells farther from circulation than a row of rooms is deep."""
        program = ctx.rules.program
        reach = program.strip_depth[1]
        for _ in range(16):
            dist = distances(corridor | access, footprint - (lobby - access))
            far = [c for c in footprint - lobby if dist.get(c, 0) > reach]
            if not far:
                break
            # A stub to the middle of the largest far piece: rooms on both sides reach it.
            piece = components(far)[0]
            cx = sum(c.x for c in piece) / len(piece)
            cy = sum(c.y for c in piece) / len(piece)
            tip = min(piece, key=lambda c: (abs(c.x - cx) + abs(c.y - cy), c))
            path = [tip, *descend(tip, dist)[1:]]
            path = _to_wall(path, footprint)
            corridor |= thicken(path, program.corridor.width, footprint) - lobby
        return corridor

    @staticmethod
    def _back_corridor(
        ctx: Context, footprint: frozenset[Cell], corridor: frozenset[Cell], width: int
    ) -> frozenset[Cell]:
        """The service door opens onto circulation: a branch to the service facade if no
        corridor reaches it (the street side is the lobby's)."""
        side = ctx.params.service_side
        if EntranceKind.SERVICE not in ctx.rules.entrances(ctx.params):
            return corridor
        if side is ctx.params.street_side:
            return corridor
        edge = [c for c in footprint if c.neighbour(side) not in footprint]
        if not edge or any(c in corridor for c in edge):
            return corridor
        dist = distances(corridor, footprint)
        x0, y0, x1, y1 = bbox(footprint)
        middle = ((x0 + x1) / 2, (y0 + y1) / 2)
        gate = min(
            (c for c in edge if c in dist),
            key=lambda c: (dist[c] + 0.2 * (abs(c.x - middle[0]) + abs(c.y - middle[1])), c),
            default=None,
        )
        if gate is None:
            return corridor
        return corridor | thicken([gate, *descend(gate, dist)], width, footprint)

    @staticmethod
    def _tidy(corridor: frozenset[Cell], lobby: frozenset[Cell], width: int) -> frozenset[Cell]:
        """Drop bits thinner than the corridor and pieces that lead nowhere."""
        kept = opened(corridor, width)
        pieces = [p for p in components(kept) if not lobby or contact(p, lobby) >= 2]
        return pieces[0] if pieces else kept

    def _cores(
        self,
        ctx: Context,
        footprint: frozenset[Cell],
        circulation: frozenset[Cell],
        lobby: frozenset[Cell],
        rng: random.Random,
    ) -> tuple[list[PlannedRoom], frozenset[Cell]]:
        rooms: list[PlannedRoom] = []
        far: frozenset[Cell] = frozenset()
        cluster: frozenset[Cell] = frozenset()  # the cores that stand together
        taken = set(circulation)
        x0, y0, x1, y1 = bbox(footprint)
        diagonal = (x1 - x0) + (y1 - y0)
        cx = sum(c.x for c in circulation) / len(circulation)
        cy = sum(c.y for c in circulation) / len(circulation)
        for entry in ctx.rules.active_core(ctx.params):
            if entry.place == "hall":
                continue
            free = footprint - taken
            options = self._core_options(free, circulation, entry.size)
            if lobby:
                # a door to the lobby on the ground floor would move upstairs
                touch = frozenset(c.neighbour(sd) for c in lobby for sd in Side)
                options = [o for o in options if o.isdisjoint(touch)] or options
            if entry.place != "far" and cluster:
                # Stairwell and lifts stand together: share a wall with the cluster if possible.
                near = frozenset(c.neighbour(sd) for c in cluster for sd in Side)
                beside = [o for o in options if len(o & near) >= 2]
                options = beside or options
            if entry.place == "far":
                # an exit of its own on the outer wall, far from the first stairwell
                outside = frozenset(
                    c for c in footprint if any(c.neighbour(sd) not in footprint for sd in Side)
                )
                first = min(rooms[0].cells) if rooms else None
                options = [
                    o
                    for o in options
                    if not o.isdisjoint(outside)
                    and (
                        first is None
                        or abs(min(o).x - first.x) + abs(min(o).y - first.y) >= FAR_CORE_MIN
                    )
                ]
            rng.shuffle(options)
            scored: list[tuple[float, frozenset[Cell]]] = []
            for option in options[:CORE_CANDIDATES]:
                rest = free - option
                slivers = sum(
                    len(p)
                    for p in components(rest)
                    if len(p) < SLIVER or thinnest_extent(p, p, 3) < 3
                )
                ox = sum(c.x for c in option) / len(option)
                oy = sum(c.y for c in option) / len(option)
                if rooms and entry.place == "far":
                    first = rooms[0].cells
                    fx = sum(c.x for c in first) / len(first)
                    fy = sum(c.y for c in first) / len(first)
                    away = (abs(ox - fx) + abs(oy - fy)) / diagonal
                    score = slivers / 10 - 2 * away
                elif rooms:
                    first = rooms[0].cells
                    fx = sum(c.x for c in first) / len(first)
                    fy = sum(c.y for c in first) / len(first)
                    score = slivers / 10 + 2 * (abs(ox - fx) + abs(oy - fy)) / diagonal
                else:
                    score = slivers / 10 + 0.5 * (abs(ox - cx) + abs(oy - cy)) / diagonal
                scored.append((score + rng.uniform(0, 0.1), option))
            if not scored:
                if entry.place == "far":
                    continue
                raise AllocationError(f"no space for the {entry.room}")
            _, cells = min(scored, key=lambda s: (s[0], min(s[1])))
            taken |= cells
            if entry.place != "far":
                cluster |= cells
            else:
                far |= cells
            rooms.append(PlannedRoom(entry.room, cells))
        return rooms, far

    @staticmethod
    def _core_options(
        free: frozenset[Cell], circulation: frozenset[Cell], size: tuple[int, int]
    ) -> list[frozenset[Cell]]:
        """Rectangles of the core's size with a long wall on the corridor."""
        found: set[frozenset[Cell]] = set()
        beside = frozenset(c.neighbour(s) for c in circulation for s in Side)
        # `size` is (along the corridor, deep): the short wall on the corridor is where the
        # stairs have their landing. The other way round only if that finds no place.
        shapes = {size}
        for cell in sorted(free):
            for side in Side:
                if cell.neighbour(side) not in circulation:
                    continue
                inward = side.opposite.delta
                along = (1, 0) if inward[0] == 0 else (0, 1)
                for length, depth in shapes:
                    for shift in (0, -(length - 1)):
                        cells = frozenset(
                            Cell(
                                cell.x + (shift + i) * along[0] + j * inward[0],
                                cell.y + (shift + i) * along[1] + j * inward[1],
                            )
                            for i in range(length)
                            for j in range(depth)
                        )
                        if cells <= free and len(cells & beside) >= min(length, 2):
                            found.add(cells)
        if not found and size[0] != size[1]:
            return PartitionLayout._core_options(free, circulation, (size[1], size[0]))
        return sorted(found, key=min)

    # --- floors -----------------------------------------------------------------------

    def _floor(
        self,
        ctx: Context,
        footprint: frozenset[Cell],
        skeleton: Skeleton,
        level: int,
        shared: dict[tuple[str, bool], tuple[list[PlannedRoom], list[str]]],
        plan: BuildingPlan,
    ) -> FloorPlan:
        role_name, role = ctx.rules.role_for(level, ctx.params)
        ground = level == 0
        core_cells = frozenset(c for r in skeleton.cores for c in r.cells)
        if role.roof is not None:
            roof = footprint - core_cells
            rooms = [*skeleton.cores, PlannedRoom(role.roof, roof)]
            return FloorPlan(level, role_name, rooms)

        circulation = [PlannedRoom("corridor", cells) for cells in components(skeleton.corridor)]
        fixed = [*circulation, *skeleton.cores]
        lobby_cells = skeleton.lobby if ground else frozenset[Cell]()
        if lobby_cells and skeleton.lobby_type:
            fixed.append(PlannedRoom(skeleton.lobby_type, lobby_cells))
        key = (role_name, ground)
        if key not in shared:
            free = footprint - skeleton.corridor - core_cells - lobby_cells
            access = skeleton.corridor | lobby_cells
            anchors: dict[str, Cell] = {}
            if skeleton.cores:
                cells = skeleton.cores[0].cells
                anchors["core"] = Cell(
                    sum(c.x for c in cells) // len(cells), sum(c.y for c in cells) // len(cells)
                )
            if ground:
                anchors["entrance"] = skeleton.main_hint
            assigner = Assigner(
                ctx.rules,
                role.rooms,
                variables(ctx.params, level),
                footprint,
                access,
                anchors,
                ctx.rules.program.cluster_filler,
                ctx.rng(f"assign:{role_name}:{ground}"),
            )
            rooms = assigner.run(components(free), f"level {level}")
            shared[key] = (rooms, assigner.warnings)
            plan.warnings += assigner.warnings
        rooms = [*fixed, *shared[key][0]]
        rooms = absorb_leftovers(rooms, ctx.rules)
        rooms = _merge_thin(rooms, ctx.rules)
        rooms = carve_stalls(rooms, ctx.rules)
        entrances = self._entrances(ctx, footprint, skeleton, rooms) if ground else []
        return FloorPlan(level, role_name, rooms, entrances)

    def _entrances(
        self,
        ctx: Context,
        footprint: frozenset[Cell],
        skeleton: Skeleton,
        rooms: list[PlannedRoom],
    ) -> list[EntranceRequest]:
        wanted = ctx.rules.entrances(ctx.params)
        hints: list[tuple[EntranceKind, Side, Cell]] = [
            (EntranceKind.MAIN, skeleton.main_side, skeleton.main_hint)
        ]
        corridor_edge = [
            (c, s) for c, s in facade(skeleton.corridor, footprint) if c in skeleton.corridor
        ]
        if EntranceKind.SERVICE in wanted:
            side = ctx.params.service_side
            if side is skeleton.main_side:
                hints.append((EntranceKind.SERVICE, side, skeleton.main_hint))
            else:
                options = [(c, s) for c, s in corridor_edge if s is side]
                if not options:
                    allowed = {
                        i
                        for i, r in enumerate(rooms)
                        if ctx.rules.spec(r.type).facade_door
                        or ctx.rules.spec(r.type).circulation
                        or r.type in ctx.rules.program.service_rooms
                    }
                    owner = {c: i for i, r in enumerate(rooms) for c in r.cells}
                    options = [
                        (c, s)
                        for c, s in facade(footprint, footprint)
                        if s is side and c not in skeleton.lobby
                    ]
                    options = [o for o in options if owner.get(o[0]) in allowed] or options
                    options.sort(
                        key=lambda o: min(
                            abs(o[0].x - k.x) + abs(o[0].y - k.y) for k in skeleton.corridor
                        )
                    )
                if options:
                    cell, _ = options[len(options) // 2] if corridor_edge else options[0]
                    hints.append((EntranceKind.SERVICE, side, cell))
        if EntranceKind.EMERGENCY in wanted and skeleton.far:
            # on the landing: the facade cell nearest the corridor the stairs are entered from
            exits = sorted(facade(skeleton.far, footprint))
            cell, side = min(
                exits,
                key=lambda e: (
                    min(abs(e[0].x - k.x) + abs(e[0].y - k.y) for k in skeleton.corridor),
                    e,
                ),
            )
            hints.append((EntranceKind.EMERGENCY, side, cell))
        elif EntranceKind.EMERGENCY in wanted:
            options = corridor_edge or [
                (c, s)
                for r in rooms
                if r.type in {e.room for e in ctx.rules.program.core}
                for c, s in facade(r.cells, footprint)
            ]
            taken = [h for _, _, h in hints]
            if options:
                cell, side = max(
                    options,
                    key=lambda o: (min(abs(o[0].x - t.x) + abs(o[0].y - t.y) for t in taken), o),
                )
                hints.append((EntranceKind.EMERGENCY, side, cell))
        result: list[EntranceRequest] = []
        for kind, side, cell in hints:
            index = next(i for i, r in enumerate(rooms) if cell in r.cells)
            result.append(EntranceRequest(kind, side, index, cell))
        return result


def _merge_thin(rooms: list[PlannedRoom], rules: Rules) -> list[PlannedRoom]:
    """A leftover piece too thin to be a room joins the neighbour it shares most wall with
    (a corridor only if nothing else touches it)."""
    fixed = {e.room for e in rules.program.core}
    result = list(rooms)
    for room in rooms:
        if not room.leftover or room not in result:
            continue
        if thinnest_extent(room.cells, room.cells) >= rules.spec(room.type).min_side:
            continue
        options: list[tuple[int, int, PlannedRoom]] = []
        for other in result:
            if other is room or other.host is not None or other.type in fixed:
                continue
            if shared := contact(room.cells, other.cells):
                options.append((not rules.spec(other.type).circulation, shared, other))
        if not options:
            continue
        _, _, other = max(options, key=lambda o: (o[0], o[1], -len(o[2].cells)))
        result[result.index(other)] = replace(other, cells=other.cells | room.cells)
        result.remove(room)
    return result


def _to_wall(path: list[Cell], footprint: frozenset[Cell]) -> list[Cell]:
    """Extend a path (far end first) straight on past its far end to the outer wall, so the
    side corridor separates the rooms either side and ends at a facade."""
    if len(path) < 2:
        return path
    dx, dy = path[0].x - path[1].x, path[0].y - path[1].y
    cell = path[0]
    extra: list[Cell] = []
    while (cell := Cell(cell.x + dx, cell.y + dy)) in footprint:
        extra.append(cell)
    return [*extra, *path]


def _lobby_entry(ctx: Context) -> RoomEntry | None:
    _, role = ctx.rules.role_for(0, ctx.params)
    values = variables(ctx.params, 0)
    return next((e for e in role.rooms if e.place == "entrance" and evaluate(e.when, values)), None)
