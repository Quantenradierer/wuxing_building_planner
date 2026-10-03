"""Corridor-first layout for footprints made of rectangles. See docs/architecture.md.

1. Split the footprint into parts: a rectangle is one part; other shapes are a main bar
   (touching the street, holding lobby and core) plus wings whose corridors start at the
   junction with their parent part (see `parts.py`).
2. Split each part's depth into bands: rows of rooms ("strips") and corridors along its
   u-axis. Shallow parts get one single-loaded corridor, deeper ones a central corridor,
   very deep ones several parallel corridors joined by a cross corridor.
3. Reserve building-wide slots: cross corridors, the vertical core and the stubs joining a
   wing's corridors to the main part (identical on every floor).
4. Per floor: reserve the ground floor's lobby and service corridor stub, then let the
   allocator fill the remaining strip segments with the floor role's rooms.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace
from itertools import pairwise

from roomplanner.errors import RulesError
from roomplanner.geometry import Axis, Cell, Side
from roomplanner.model import level_name
from roomplanner.params import EntranceKind
from roomplanner.pipeline.base import (
    AllocationError,
    BuildingPlan,
    Context,
    EntranceRequest,
    FloorPlan,
    PlannedRoom,
)
from roomplanner.pipeline.layout.allocation import Allocator, Anchors, Segment
from roomplanner.pipeline.layout.frame import (
    Band,
    BandKind,
    Box,
    Frame,
    Grid,
    Interval,
    LocalSide,
    free_intervals,
)
from roomplanner.pipeline.layout.hallways import trim_hallways
from roomplanner.pipeline.layout.leftovers import absorb_leftovers
from roomplanner.pipeline.layout.parts import decompose, wing_frame
from roomplanner.pipeline.layout.stalls import carve_stalls
from roomplanner.pipeline.registry import register
from roomplanner.rules import (
    BalconyRule,
    CoreEntry,
    FloorRole,
    Priority,
    RoomEntry,
    evaluate,
    variables,
)

MIN_GAP_CELLS = 3  # free space left next to reserved slots, if any: the smallest room
BACK_ROOM_SLACK = 1.5  # a back room may be this much over its maximum area, else a stub
INTERIOR_STRIP_MIN = 6  # cells; back-to-back strips between parallel corridors
PARK_SHARE = 0.55  # of the main part's length, at most, for a car park
PARK_ASPECT = 1.5  # a car park is at most this much longer or shorter than deep
FAR_CORE_MIN = 24  # cells (12 m) at least between the core and a second stairwell


@dataclass
class Part:
    """One rectangle of the footprint with its bands and building-wide reservations."""

    frame: Frame
    grid: Grid
    bands: list[Band]
    cross: Interval | None = None
    reserved: dict[int, list[Interval]] = field(default_factory=dict[int, list[Interval]])
    # u-ranges of strips whose back is a junction with another part instead of a facade
    no_facade: dict[int, list[Interval]] = field(default_factory=dict[int, list[Interval]])
    connectors: list[PlannedRoom] = field(default_factory=list[PlannedRoom])

    def blocked(self, band: Band, *extra: Interval | None) -> list[Interval]:
        spans = [*self.reserved.get(band.index, []), *(e for e in extra if e is not None)]
        if self.cross is not None and not band.facade:  # the cross only joins the corridors
            spans.append(self.cross)
        return spans


@dataclass
class Core:
    """A full-depth slot holding core rooms, the same on every floor."""

    part: Part
    band: Band
    slot: Interval
    rooms: list[PlannedRoom]
    # Rectangles behind the core rooms: rooms of their own on every floor
    back: list[frozenset[Cell]]


@dataclass
class Skeleton:
    """Everything shared by all floors."""

    main: Part
    wings: list[Part]
    # The core slot first, then the far one (`place: far`: a second stairwell)
    cores: list[Core] = field(default_factory=list[Core])
    core_rooms: list[PlannedRoom] = field(default_factory=list[PlannedRoom])
    lobby_slice: Interval | None = None  # ground-floor lobby across the main part's short end
    # (level, room, slice): a `place: end` room (basement car park) across one short end
    park: tuple[int, str, Interval] | None = None
    footprint: frozenset[Cell] = frozenset()

    @property
    def parts(self) -> list[Part]:
        return [self.main, *self.wings]


@register("layout", "corridor")
class CorridorLayout:
    def main_min_depth(self, ctx: Context) -> int:
        """Depth the main part needs; subclasses with other band patterns override it."""
        return ctx.rules.program.corridor.width + ctx.rules.program.strip_depth[0]

    def main_corridor(self, ctx: Context, frame: Frame) -> int:
        """Width of the main part's corridors (0: none)."""
        return ctx.rules.program.corridor.width

    def check_feasibility(self, ctx: Context, footprint: frozenset[Cell]) -> list[str]:
        program = ctx.rules.program
        corridor = program.corridor.width
        wing_depth = corridor + program.strip_depth[0]
        problems: list[str] = []
        pieces = decompose(footprint, ctx.params.street_side, self.main_min_depth(ctx), wing_depth)
        frames = [("building", Frame.for_rect(pieces[0].box), self.main_min_depth(ctx))]
        main_corridor = self.main_corridor(ctx, frames[0][1])
        for piece in pieces[1:]:
            assert piece.junction is not None
            frames.append(("wing", wing_frame(piece.box, piece.junction), wing_depth))
        for name, frame, min_depth in frames:
            if frame.depth < min_depth:
                problems.append(
                    f"{name} is {frame.depth} cells across, needs at least "
                    f"{min_depth} for its corridor and rooms"
                )
        core = sum(c.size[0] * c.size[1] for c in ctx.rules.active_core(ctx.params))
        corridors = main_corridor * frames[0][1].length
        corridors += sum(corridor * frame.length for _, frame, _ in frames[1:])
        for level in ctx.params.levels:
            _, role = ctx.rules.role_for(level, ctx.params)
            needed = core + corridors
            for entry in role.rooms:
                if entry.priority is Priority.REQUIRED and evaluate(
                    entry.when, variables(ctx.params, level)
                ):
                    low = (entry.area or ctx.rules.spec(entry.room).area)[0]
                    needed += low * entry.count_range[0]
            if needed > len(footprint):
                problems.append(
                    f"{level_name(level)}: core, corridor and required rooms need "
                    f"{needed} cells, the floor has {len(footprint)}"
                )
        return problems

    def layout(self, ctx: Context, footprint: frozenset[Cell]) -> BuildingPlan:
        rng = ctx.rng("layout")
        skeleton = self._skeleton(ctx, footprint, rng)
        skeleton.footprint = footprint
        plan = BuildingPlan(floors=[])
        for part in skeleton.parts:
            axis = Axis.H if part.frame.u_is_x else Axis.V
            if axis not in plan.facade_grid:
                offset = part.frame.absolute_grid_offset(part.grid)
                plan.facade_grid[axis] = (offset, part.grid.module)
        for level in ctx.params.levels:
            plan.floors.append(self._floor(ctx, skeleton, level, rng, plan.warnings))
        return plan

    # --- skeleton ---------------------------------------------------------------------

    def _skeleton(self, ctx: Context, footprint: frozenset[Cell], rng: random.Random) -> Skeleton:
        program = ctx.rules.program
        corridor_width = program.corridor.width
        pieces = decompose(
            footprint,
            ctx.params.street_side,
            self.main_min_depth(ctx),
            corridor_width + program.strip_depth[0],
        )

        main_frame = Frame.for_rect(pieces[0].box)
        street = main_frame.local(ctx.params.street_side)
        junctions = [p.junction for p in pieces[1:] if p.parent == 0 and p.junction]
        junction = main_frame.local(junctions[0]) if junctions else None
        parts = [self._part(ctx, main_frame, street, rng, junction, main=True)]
        for piece in pieces[1:]:
            assert piece.parent is not None and piece.junction is not None
            frame = wing_frame(piece.box, piece.junction)
            wing = self._part(ctx, frame, frame.local(ctx.params.street_side), rng)
            parent = parts[piece.parent]
            self._connect(ctx, parent, wing, parent.frame.local(piece.junction))
            parts.append(wing)

        main = parts[0]
        skeleton = Skeleton(main, parts[1:])
        lobby = self._lobby_entry(ctx)
        grid, frame = main.grid, main.frame
        if lobby is not None and street.is_end:
            length = max(
                math.ceil(self._area(lobby, ctx, rng) / frame.depth),
                ctx.rules.spec(lobby.room).min_side,
            )
            if street is LocalSide.U0:
                lobby_slice = Interval(0, grid.ceil(length))
            else:
                lobby_slice = Interval(grid.floor(frame.length - length), frame.length)
            reserved = [i for spans in main.reserved.values() for i in spans]
            skeleton.lobby_slice = _absorb_gaps(lobby_slice, reserved, frame.length, _min_gap(grid))

        skeleton.park = self._park_slice(ctx, main, skeleton.lobby_slice, rng)

        # Parallel corridors of the main part need a cross corridor; a wing's corridors are
        # already joined through their connectors into the main part.
        for part in [main]:
            if sum(b.kind is BandKind.CORRIDOR for b in part.bands) > 1:
                length = part.frame.length
                target = length / 2 + rng.uniform(-1, 1) * length / 8
                blocked = [skeleton.lobby_slice] if part is main and skeleton.lobby_slice else []
                blocked += [skeleton.park[2]] if part is main and skeleton.park else []
                blocked += [i for spans in part.reserved.values() for i in spans]
                # As wide as the corridors: it only crosses the interior strips, which
                # don't follow the facade grid.
                part.cross = self._choose(part.grid, corridor_width, target, blocked)
                if part.cross is None:
                    raise AllocationError("no space for the cross corridor")

        core = ctx.rules.active_core(ctx.params)
        if entries := [c for c in core if c.place is None]:
            band = self._core_band(main, street, rng)
            park = skeleton.park[2] if skeleton.park else None
            blocked = main.blocked(band, skeleton.lobby_slice, park)
            found = self._core(ctx, main, band, entries, blocked, rng, skeleton.lobby_slice)
            if found is None:
                raise AllocationError("no space for the core")
            skeleton.cores.append(found)
            skeleton.core_rooms += found.rooms
            if far := [c for c in core if c.place == "far"]:
                self._far_core(ctx, skeleton, far, rng)
        if in_hall := [c for c in core if c.place == "hall"]:
            skeleton.core_rooms += self._hall_core(ctx, main, in_hall, rng)
        return skeleton

    @staticmethod
    def _park_slice(
        ctx: Context, main: Part, lobby: Interval | None, rng: random.Random
    ) -> tuple[int, str, Interval] | None:
        """The `place: end` room of a basement level (a car park) claims the whole depth of
        one end of the main part before anything else: the cores and the cross corridor
        keep out of it. Not at the lobby's end, not across a wing's junction."""
        frame, grid = main.frame, main.grid
        for level in ctx.params.levels:
            role = ctx.rules.role_for(level, ctx.params)[1]
            values = variables(ctx.params, level)
            entry = next(
                (e for e in role.rooms if e.place == "end" and evaluate(e.when, values)), None
            )
            if entry is not None:
                break
        else:
            return None
        spec = ctx.rules.spec(entry.room)
        low, high = entry.area or spec.area
        # Roughly square: the area asks for a length, kept within `PARK_ASPECT` of the depth
        # (a deep building gets a bigger deck than `high`), but never below `low`.
        shortest = math.ceil(frame.depth / PARK_ASPECT)
        longest = math.ceil(frame.depth * PARK_ASPECT)
        want = min(
            max(shortest, min(longest, math.ceil(high / frame.depth))),
            int(frame.length * PARK_SHARE),
        )
        want = max(want, min(math.ceil(low / frame.depth), int(frame.length * PARK_SHARE)))
        obstacles = [i for spans in main.reserved.values() for i in spans]
        options: list[Interval] = []
        near = min((o.u0 for o in obstacles), default=frame.length) - MIN_GAP_CELLS
        end = grid.floor(min(near, want))
        if (lobby is None or lobby.u0 > 0) and end >= spec.min_side:
            options.append(Interval(0, end))
        far = max((o.u1 for o in obstacles), default=0) + MIN_GAP_CELLS
        start = grid.ceil(max(far, frame.length - want))
        if (lobby is None or lobby.u1 < frame.length) and start <= frame.length - spec.min_side:
            options.append(Interval(start, frame.length))
        options = [o for o in options if o.width * frame.depth >= low]
        if not options:
            return None
        return level, entry.room, rng.choice(options)

    def _far_core(
        self, ctx: Context, skeleton: Skeleton, entries: list[CoreEntry], rng: random.Random
    ) -> None:
        """A second core slot (stairwell) at the part end farthest from the core: an end of
        the main part or a wing's far end. None if no end is at least `FAR_CORE_MIN` away."""
        first = skeleton.cores[0]
        centre = first.part.frame.cell(int(first.slot.centre), (first.band.v0 + first.band.v1) // 2)

        def distance(cell: Cell) -> int:
            return abs(cell.x - centre.x) + abs(cell.y - centre.y)

        ends: list[tuple[int, Part, LocalSide]] = []
        for part in skeleton.parts:
            frame = part.frame
            # A wing's u runs away from its junction: only its far end.
            for end in [LocalSide.U0, LocalSide.U1] if part is skeleton.main else [LocalSide.U1]:
                u = 0 if end is LocalSide.U0 else frame.length - 1
                ends.append((distance(frame.cell(u, frame.depth // 2)), part, end))
        street = ctx.params.street_side
        for _, part, end in sorted(ends, key=lambda e: -e[0]):
            band = self._core_band(part, part.frame.local(street), rng)
            lobby = skeleton.lobby_slice if part is skeleton.main else None
            taken = [c.slot for c in skeleton.cores if c.band is band]
            blocked = part.blocked(band, lobby, *taken)
            if part is skeleton.main and skeleton.park:
                blocked.append(skeleton.park[2])
            try:
                found = self._core(ctx, part, band, entries, blocked, rng, lobby, end)
            except AllocationError:  # a strip too shallow for it
                continue
            if found is None:
                continue
            middle = part.frame.cell(int(found.slot.centre), (band.v0 + band.v1) // 2)
            if distance(middle) >= FAR_CORE_MIN:
                skeleton.cores.append(found)
                skeleton.core_rooms += found.rooms
                return

    def _hall_core(
        self, ctx: Context, main: Part, entries: list[CoreEntry], rng: random.Random
    ) -> list[PlannedRoom]:
        """Core rooms with `place: hall`; only the hall layout has a hall."""
        raise RulesError(f"{ctx.rules.program.building}: `place: hall` core rooms need a hall")

    def _part(
        self,
        ctx: Context,
        frame: Frame,
        street: LocalSide,
        rng: random.Random,
        junction: LocalSide | None = None,
        main: bool = False,
    ) -> Part:
        """Bands of one part; `junction` is the main part's side a wing is attached to."""
        grid = Grid.centred(ctx.rules.program.facade.module, frame.length)
        bands = self._bands(ctx, frame, ctx.rules.program.corridor.width, street, rng)
        return Part(frame, grid, bands)

    def _connect(self, ctx: Context, parent: Part, wing: Part, junction: LocalSide) -> None:
        """Corridor stubs through the parent part's strips so the wing's corridors connect."""
        corridor_width = ctx.rules.program.corridor.width
        wing_corridors = [b for b in wing.bands if b.kind is BandKind.CORRIDOR]
        frame = parent.frame
        if not junction.is_end:
            band = parent.bands[0] if junction is LocalSide.V0 else parent.bands[-1]
            whole = _u_range(frame, [wing.frame.cell(0, v) for v in range(wing.frame.depth)])
            parent.no_facade.setdefault(band.index, []).append(whole)
            if band.kind is BandKind.CORRIDOR:
                return
            spans: list[Interval] = []
            for corridor in wing_corridors:
                cells = [wing.frame.cell(0, v) for v in range(corridor.v0, corridor.v1)]
                spans.append(_u_range(frame, cells))
            for span in spans:
                others = [s for s in spans if s != span]
                others += parent.reserved.get(band.index, [])
                span = _absorb_gaps(span, others, frame.length, _min_gap(parent.grid))
                # Stubs of several wings may now overlap: join them.
                for taken in [r for r in parent.reserved.get(band.index, []) if r.overlaps(span)]:
                    span = Interval(min(span.u0, taken.u0), max(span.u1, taken.u1))
                parent.reserved.setdefault(band.index, []).append(span)
                parent.connectors.append(
                    PlannedRoom("corridor", frame.rect(span.u0, span.u1, band.v0, band.v1))
                )
            return
        width = parent.grid.round_up(corridor_width)
        end = (
            Interval(0, width)
            if junction is LocalSide.U0
            else Interval(frame.length - width, frame.length)
        )
        for corridor in wing_corridors:
            cells = [wing.frame.cell(0, v) for v in range(corridor.v0, corridor.v1)]
            v_values = {frame.to_local(c.x + 0.5, c.y + 0.5)[1] for c in cells}
            v_lo, v_hi = math.floor(min(v_values)), math.floor(max(v_values)) + 1
            overlapping = [b for b in parent.bands if b.v0 < v_hi and v_lo < b.v1]
            if any(b.kind is BandKind.CORRIDOR for b in overlapping):
                continue
            for band in overlapping:
                taken = parent.reserved.get(band.index, [])
                if any(t.u0 <= end.u0 and end.u1 <= t.u1 for t in taken):
                    continue
                span = _absorb_gaps(end, taken, frame.length, _min_gap(parent.grid))
                parent.reserved.setdefault(band.index, []).append(span)
                parent.connectors.append(
                    PlannedRoom("corridor", frame.rect(span.u0, span.u1, band.v0, band.v1))
                )

    def _bands(
        self, ctx: Context, frame: Frame, corridor: int, street: LocalSide, rng: random.Random
    ) -> list[Band]:
        low, high = ctx.rules.program.strip_depth
        depth = frame.depth
        # Single-loaded only while one row is not too deep; otherwise two rows, even if
        # they are a bit shallower than `low` (deep rows make oversized rooms).
        if depth - corridor <= high or (depth - corridor) // 2 < _shallowest(low):
            # Single-loaded: one row of rooms, on the street side if the street is a long side.
            corridor_first = street is LocalSide.V1 if not street.is_end else rng.random() < 0.5
            if corridor_first:
                return [
                    Band(0, BandKind.CORRIDOR, 0, corridor),
                    Band(1, BandKind.STRIP, corridor, depth, LocalSide.V0, facade=True),
                ]
            return [
                Band(0, BandKind.STRIP, 0, depth - corridor, LocalSide.V1, facade=True),
                Band(1, BandKind.CORRIDOR, depth - corridor, depth),
            ]

        depths = self._strip_depths(depth, corridor, low, high, rng)
        count = len(depths) // 2

        bands: list[Band] = []
        v = 0
        for k in range(count):
            a, b = depths[2 * k], depths[2 * k + 1]
            bands.append(Band(len(bands), BandKind.STRIP, v, v + a, LocalSide.V1, facade=v == 0))
            bands.append(Band(len(bands), BandKind.CORRIDOR, v + a, v + a + corridor))
            v += a + corridor
            bands.append(
                Band(len(bands), BandKind.STRIP, v, v + b, LocalSide.V0, facade=v + b == depth)
            )
            v += b
        return bands

    @staticmethod
    def _strip_depths(
        depth: int, corridor: int, low: int, high: int, rng: random.Random
    ) -> list[int]:
        """Depths of the 2k strips around k corridors (k >= 1).

        Facade strips stay within [low, high]; with k > 1 the back-to-back interior strips
        between the corridors take the rest (thin ones hold toilets, storage and the core).
        """
        total = depth - corridor
        if total <= 2 * high:
            lo, hi = max(low, total - high), min(high, total - low)
            if lo > hi:
                return [total // 2, total - total // 2]
            spread = (hi - lo) // 4
            first = min(hi, max(lo, (lo + hi) // 2 + rng.randint(-spread, spread)))
            return [first, total - first]
        interior_min = INTERIOR_STRIP_MIN
        for k in range(2, depth):
            interiors = 2 * k - 2
            facade = min(high, (depth - k * corridor - interiors * interior_min) // 2)
            if facade < low:
                break
            rest = depth - k * corridor - 2 * facade
            if rest / interiors <= high:
                inner = [rest // interiors] * interiors
                for i in range(rest - sum(inner)):
                    inner[i] += 1
                return [facade, *inner, facade]
        # Between one and two corridors: deeper facade strips.
        return [total // 2, total - total // 2]

    def _core_band(self, part: Part, street: LocalSide, rng: random.Random) -> Band:
        strips = [b for b in part.bands if b.kind is BandKind.STRIP]
        if part.cross is not None:
            return rng.choice([b for b in strips if not b.facade])
        if len(strips) == 2 and not street.is_end:
            street_strip = strips[0] if street is LocalSide.V0 else strips[1]
            return next(b for b in strips if b is not street_strip)
        return rng.choice(strips)

    def _core(
        self,
        ctx: Context,
        part: Part,
        band: Band,
        entries: list[CoreEntry],
        blocked: list[Interval],
        rng: random.Random,
        lobby: Interval | None = None,
        end: LocalSide | None = None,
    ) -> Core | None:
        """The core occupies one full-depth slot: its rooms and the rectangles behind them.
        `end`: as close to that end of the part as it fits (the second stairwell)."""
        frame, grid = part.frame, part.grid
        sizes: list[tuple[int, int]] = []  # (u, v) per entry
        for entry in entries:
            short, long = sorted(entry.size)
            if short > band.depth:
                raise AllocationError(f"{entry.room} does not fit a {band.depth}-cell strip")
            # Too long for the strip: shorter while it keeps its minimum area, else across.
            if long > band.depth and short * band.depth >= ctx.rules.spec(entry.room).area[0]:
                long = band.depth
            sizes.append((short, long) if long <= band.depth else (long, short))
        width = sum(u for u, _ in sizes)
        if band.facade:
            width = grid.round_up(width)

        if end is not None:
            target = 0 if end is LocalSide.U0 else frame.length
            slot = self._choose(grid, width, target, blocked, optional=lobby)
        elif part.cross is not None:
            left = part.cross.u0 - width / 2
            right = part.cross.u1 + width / 2
            target = left if rng.random() < 0.5 else right
            slot = self._choose(grid, width, target, blocked, touching=part.cross, optional=lobby)
        else:
            target = frame.length / 2 + rng.uniform(-1, 1) * frame.length / 6
            slot = self._choose(grid, width, target, blocked, optional=lobby)
        if slot is None or slot.width < width:  # a part too short for it
            return None

        # Each room at its own size on the corridor side (the stairwell also takes the slot's
        # partial module); the space behind them is rooms of their own on every floor. Rests
        # too thin for a room make the core rooms deeper instead.
        thin = ctx.rules.spec(ctx.rules.program.cluster_filler).min_side
        core_depth = max(v for _, v in sizes)
        depth = band.depth if band.depth - core_depth < thin else core_depth

        def reach(v: int) -> int:
            return depth if depth - v < thin else v

        rooms: list[PlannedRoom] = []
        behind: list[frozenset[Cell]] = []
        u1 = slot.u1
        u_sizes = [slot.width - sum(u for u, _ in sizes[1:]), *(u for u, _ in sizes[1:])]
        for entry, u_size, (_, v_size) in zip(entries[1:], u_sizes[1:], sizes[1:], strict=True):
            cells = frame.rect(u1 - u_size, u1, *self._from_corridor(band, reach(v_size)))
            rooms.append(PlannedRoom(entry.room, cells))
            behind.append(self._depth_rect(frame, band, u1 - u_size, u1, reach(v_size), depth))
            u1 -= u_size
        v_stairs = reach(sizes[0][1])
        stairs = frame.rect(slot.u0, u1, *self._from_corridor(band, v_stairs))
        rooms.insert(0, PlannedRoom(entries[0].room, stairs))
        behind.append(self._depth_rect(frame, band, slot.u0, u1, v_stairs, depth))
        behind.append(self._depth_rect(frame, band, slot.u0, slot.u1, depth, band.depth))
        return Core(part, band, slot, rooms, [cells for cells in behind if cells])

    def _depth_rect(
        self, frame: Frame, band: Band, u0: int, u1: int, d0: int, d1: int
    ) -> frozenset[Cell]:
        """Cells from `d0` to `d1` away from the strip's corridor side."""
        if d1 <= d0:
            return frozenset()
        near, far = self._from_corridor(band, d0), self._from_corridor(band, d1)
        v0, v1 = (near[1], far[1]) if band.corridor_at is LocalSide.V0 else (far[0], near[0])
        return frame.rect(u0, u1, v0, v1)

    @staticmethod
    def _from_corridor(band: Band, size: int) -> tuple[int, int]:
        """v-range of `size` cells on the strip's corridor side."""
        if band.corridor_at is LocalSide.V0:
            return band.v0, band.v0 + size
        return band.v1 - size, band.v1

    @staticmethod
    def _choose(
        grid: Grid,
        width: int,
        target: float,
        blocked: list[Interval],
        touching: Interval | None = None,
        avoid: tuple[Interval, ...] = (),
        optional: Interval | None = None,
    ) -> Interval | None:
        """Grid-aligned interval closest to `target` that leaves usable gaps around it.

        `avoid` intervals must not be overlapped but are no neighbours (e.g. junctions).
        `optional` is one of `blocked` that exists on some floors only (the ground-floor
        lobby): the gaps must be usable with and without it.
        """
        min_gap = _min_gap(grid)
        starts = {*grid.points(), *(b.u1 for b in blocked), *(b.u0 - width for b in blocked)}
        options = {Interval(start, start + width) for start in starts}
        # Flush with an end of the part, widened to the next grid point (partial modules).
        options |= {
            Interval(0, grid.ceil(width)),
            Interval(grid.floor(grid.length - width), grid.length),
        }
        candidates: list[Interval] = []
        for interval in options:
            start = interval.u0
            if start < 0 or interval.u1 > grid.length:
                continue
            if any(interval.overlaps(b) for b in (*blocked, *avoid)):
                continue
            if touching is not None and interval.gap_to(touching) != 0:
                continue
            variants = [blocked]
            if optional is not None:
                variants.append([b for b in blocked if b != optional])
            if any(_cramped(interval, spans, grid.length, min_gap) for spans in variants):
                continue
            candidates.append(interval)
        if not candidates:
            return None
        return min(candidates, key=lambda i: (abs(i.centre - target), i.u0))

    # --- floors -----------------------------------------------------------------------

    def _floor(
        self,
        ctx: Context,
        skeleton: Skeleton,
        level: int,
        rng: random.Random,
        warnings: list[str],
    ) -> FloorPlan:
        main = skeleton.main
        role_name, role = ctx.rules.role_for(level, ctx.params)
        rooms: list[PlannedRoom] = list(skeleton.core_rooms)
        extra: dict[int, list[Interval]] = {}  # this floor's reservations in the main part
        hints: list[tuple[EntranceKind, Side, Cell]] = []
        slice_ = skeleton.lobby_slice if level == 0 else None
        park = skeleton.park if skeleton.park and skeleton.park[0] == level else None
        if park is not None:  # claimed first: the whole depth of one end of the building
            slice_ = park[2]

        for core in skeleton.cores:
            if core.part is main:
                extra.setdefault(core.band.index, []).append(core.slot)
        if role.roof is not None:
            return self._roof(ctx, skeleton, level, role_name, role.roof, rooms, extra)
        slice_cells = (
            main.frame.rect(slice_.u0, slice_.u1, 0, main.frame.depth)
            if slice_
            else frozenset[Cell]()
        )
        if park is not None:
            rooms.append(PlannedRoom(park[1], slice_cells))
        for part in skeleton.parts:
            for connector in part.connectors:
                if cells := connector.cells - slice_cells:
                    rooms.append(PlannedRoom(connector.type, cells))
            if part.cross is not None:
                for band in part.bands:
                    if band.kind is BandKind.STRIP and not band.facade:
                        cells = part.frame.rect(part.cross.u0, part.cross.u1, band.v0, band.v1)
                        rooms.append(PlannedRoom("corridor", cells))

        entrance: Box | None = None
        reserved: list[str] = []  # the back room, standing in for a counted room
        if level == 0:
            planned = len(rooms)
            entrance = self._ground_floor(ctx, skeleton, rooms, extra, hints, rng, warnings)
            lobby_entry = self._lobby_entry(ctx)
            skip = {"corridor", lobby_entry.room if lobby_entry else None}
            reserved = [r.type for r in rooms[planned:] if r.type not in skip]
            self._emergency_exit(ctx, skeleton, hints, warnings)

        segments: list[Segment] = []
        # A balcony on this floor, or on one below: then this floor is set back above it.
        balcony_role = role if role.balcony is not None else None
        for lower in range(1, level):
            below = ctx.rules.role_for(lower, ctx.params)[1]
            if balcony_role is None and below.balcony is not None:
                balcony_role = below
        balcony_band = self._balcony_band(ctx, main) if balcony_role is not None else None
        cut: frozenset[Cell] = frozenset()
        core_cells = frozenset(c for r in skeleton.core_rooms for c in r.cells)
        for part in skeleton.parts:
            part_slice = slice_ if part is main else None
            for band in part.bands:
                if band.kind is BandKind.HALL:
                    hall = next((e.room for e in role.rooms if e.place == "hall"), None)
                    if hall is None:
                        raise RulesError(f"floor role {role_name} needs a `place: hall` room")
                    # Connectors crossing the hall split it: one hall room per piece,
                    # slivers too thin for a room join the connector corridor.
                    minimum = ctx.rules.spec(hall).min_side
                    rules = ctx.rules.program.hall
                    # Without a service corridor the back rooms open onto the hall.
                    hub = not any(b.kind is BandKind.CORRIDOR for b in part.bands)
                    foyer = rules.foyer if rules and level == 0 and part is main else None
                    street = part.frame.local(ctx.params.street_side)
                    for span in free_intervals(part.frame.length, part.blocked(band)):
                        cells = part.frame.rect(span.u0, span.u1, band.v0, band.v1)
                        cells -= core_cells  # public stairs stand in the hall
                        kind = hall if span.width >= minimum else "corridor"
                        if foyer is not None and kind == hall:
                            lobby = _foyer(part.frame, span, band, street, foyer.depth, minimum)
                            if lobby:
                                rooms.append(PlannedRoom(foyer.room, lobby))
                                cells -= lobby
                        rooms.append(PlannedRoom(kind, cells, hub=hub))
                    continue
                if band.kind is BandKind.CORRIDOR:
                    blocked = [part_slice] if part_slice else []
                    for span in free_intervals(part.frame.length, blocked):
                        cells = part.frame.rect(span.u0, span.u1, band.v0, band.v1)
                        rooms.append(PlannedRoom("corridor", cells))
                    continue
                if part is main:
                    own = extra.get(band.index, [])
                else:  # a second stairwell at a wing's end
                    own = [c.slot for c in skeleton.cores if c.part is part and c.band is band]
                blocked = part.blocked(band, part_slice, *own)
                if part is main and balcony_role is not None and band is balcony_band:
                    found = self._balcony(ctx, part, band, blocked, balcony_role)
                    if found is not None and balcony_role.balcony is not None:
                        cells, span, back = found
                        if balcony_role is role:
                            rooms.append(PlannedRoom(balcony_role.balcony.room, cells))
                        else:  # open to the sky: not part of this floor
                            cut = cells
                        blocked = [*blocked, span]
                        if back.depth > 0:  # a loggia takes the whole row
                            segments.append(
                                Segment(part.frame, back, span, True, part.grid.module, part.grid)
                            )
                segments += self._segments(part, band, blocked)

        core_box = None
        if skeleton.cores:
            band, slot = skeleton.cores[0].band, skeleton.cores[0].slot
            core_box = main.frame.box(slot.u0, slot.u1, band.v0, band.v1)
        service = next((c for kind, _, c in hints if kind is EntranceKind.SERVICE), None)
        service_box = Box(service.x, service.y, service.x + 1, service.y + 1) if service else None
        lobby = self._lobby_entry(ctx) if level == 0 else None
        anchors = Anchors(core_box, entrance, service_box, lobby.room if lobby else None)
        allocator = Allocator(ctx, ctx.rules, segments, anchors, ctx.rng(f"allocate:{level}"))
        behind: list[tuple[Segment, frozenset[Cell]]] = []
        for core in skeleton.cores:
            frame, grid = core.part.frame, core.part.grid
            segment = Segment(frame, core.band, core.slot, core.band.facade, 1, grid)
            behind += [(segment, cells) for cells in core.back]
        existing = [r.type for r in rooms]
        rooms += allocator.allocate(role, level, level_name(level), behind, existing, reserved)
        warnings += allocator.warnings

        rooms = absorb_leftovers(rooms, ctx.rules)
        rooms = trim_hallways(rooms, ctx.rules, frozenset(hint for _, _, hint in hints))
        rooms = _merge_corridors(rooms)
        # No stall where an exterior door goes (the front office's WC beside the entrance).
        reach = max(rule.width for rule in ctx.rules.entrances(ctx.params).values())
        keep = frozenset(
            cell
            for _, _, hint in hints
            for dx in range(-reach, reach + 1)
            for dy in range(-reach, reach + 1)
            if (cell := Cell(hint.x + dx, hint.y + dy)) in rooms[_room_index(rooms, hint)].cells
        )
        rooms = carve_stalls(rooms, ctx.rules, keep=keep)
        entrances = [
            EntranceRequest(kind, side, _room_index(rooms, hint), hint)
            for kind, side, hint in hints
        ]
        return FloorPlan(level, role_name, rooms, entrances, cut)

    def _roof(
        self,
        ctx: Context,
        skeleton: Skeleton,
        level: int,
        role_name: str,
        roof: str,
        core: list[PlannedRoom],
        extra: dict[int, list[Interval]],
    ) -> FloorPlan:
        """A flat roof: the stair housing and lift shafts rise through it, the rest is one
        open-air room, less the sky over a balcony below."""
        cores = {c.room for c in ctx.rules.program.core}
        rooms = [r for r in core if r.type in cores]
        cut: frozenset[Cell] = frozenset()
        main = skeleton.main
        for lower in range(1, level):
            below = ctx.rules.role_for(lower, ctx.params)[1]
            band = self._balcony_band(ctx, main) if below.balcony is not None else None
            if band is not None:
                blocked = main.blocked(band, None, *extra.get(band.index, []))
                if (found := self._balcony(ctx, main, band, blocked, below)) is not None:
                    cut = found[0]
        taken = {c for r in rooms for c in r.cells}
        rooms.append(PlannedRoom(roof, skeleton.footprint - taken - cut))
        return FloorPlan(level, role_name, rooms, [], cut)

    @staticmethod
    def _balcony_band(ctx: Context, main: Part) -> Band | None:
        """The facade strip a balcony goes on: the street side's, else the deepest."""
        strips = [b for b in main.bands if b.kind is BandKind.STRIP and b.facade]
        if not strips:
            return None
        street = main.frame.local(ctx.params.street_side)
        on_street = [
            b
            for b in strips
            if (street is LocalSide.V0 and b.v0 == 0)
            or (street is LocalSide.V1 and b.v1 == main.frame.depth)
        ]
        return (on_street or sorted(strips, key=lambda b: -b.depth))[0]

    @staticmethod
    def _balcony(
        ctx: Context, part: Part, band: Band, blocked: list[Interval], role: FloorRole
    ) -> tuple[frozenset[Cell], Interval, Band] | None:
        """Balcony cells at the facade of the strip, the stretch it takes and the shallower
        strip behind it, if the rooms there still fit. The kind is picked per building (the
        same on every floor); if it doesn't fit, the others are tried in turn."""
        rule = role.balcony
        spans = free_intervals(part.frame.length, blocked)
        if rule is None or not spans:
            return None
        first = ctx.rng("balcony").choice(rule.kinds)
        fill = min(ctx.rules.spec(e.room).min_side for e in role.rooms if e.fill)
        for kind in [first, *(k for k in rule.kinds if k != first)]:
            found = CorridorLayout._balcony_kind(part, band, spans, rule, kind, fill)
            if found is not None:
                return found
        return None

    @staticmethod
    def _balcony_kind(
        part: Part, band: Band, spans: list[Interval], rule: BalconyRule, kind: str, fill: int
    ) -> tuple[frozenset[Cell], Interval, Band] | None:
        grid = part.grid
        longest = max(spans, key=lambda s: s.width)
        depth = rule.depth
        width = grid.module * rule.modules
        if kind == "corner":
            corners = [s for s in spans if s.u0 == 0 or s.u1 == part.frame.length]
            if not corners:
                return None
            corner = max(corners, key=lambda s: s.width)
            if corner.u0 == 0:
                start, end = 0, min(grid.floor(corner.u1), grid.round_up(width))
            else:
                start, end = max(grid.ceil(corner.u0), part.frame.length - width), corner.u1
            depth = rule.depth + grid.module  # a corner terrace is deeper
        else:
            if kind == "strip":
                width = grid.round_up(round(longest.width * rule.share))
            start = grid.ceil(longest.u0 + (longest.width - width) // 2)
            end = min(grid.floor(longest.u1), start + width)
            if kind == "room":
                depth = band.depth  # a loggia the whole row deep, off the corridor
        depth = min(depth, band.depth)
        rest = band.depth - depth
        if end - start < 2 * grid.module or 0 < rest < fill:
            return None
        span = Interval(start, end)
        if band.v0 == 0:  # facade at v0: the balcony there, the rooms behind it
            cells = part.frame.rect(start, end, band.v0, band.v0 + depth)
            back = replace(band, v0=band.v0 + depth)
        else:
            cells = part.frame.rect(start, end, band.v1 - depth, band.v1)
            back = replace(band, v1=band.v1 - depth)
        return cells, span, back

    @staticmethod
    def _segments(part: Part, band: Band, blocked: list[Interval]) -> list[Segment]:
        """Free spans of a strip, split where its back changes between facade and junction."""
        segments: list[Segment] = []
        junctions = part.no_facade.get(band.index, [])
        min_piece = 2 * part.grid.module
        for span in free_intervals(part.frame.length, blocked):
            inner = {p for j in junctions for p in (j.u0, j.u1) if span.u0 < p < span.u1}
            # Don't cut off slivers: only cut where both sides stay usable.
            cuts = [span.u0]
            for point in sorted(inner):
                if point - cuts[-1] >= min_piece and span.u1 - point >= min_piece:
                    cuts.append(point)
            cuts.append(span.u1)
            for u0, u1 in pairwise(cuts):
                piece = Interval(u0, u1)
                facade = band.facade and not any(piece.overlaps(j) for j in junctions)
                unit = part.grid.module if facade else 1
                segments.append(Segment(part.frame, band, piece, facade, unit, part.grid))
        return segments

    def _ground_floor(
        self,
        ctx: Context,
        skeleton: Skeleton,
        rooms: list[PlannedRoom],
        extra: dict[int, list[Interval]],
        hints: list[tuple[EntranceKind, Side, Cell]],
        rng: random.Random,
        warnings: list[str],
    ) -> Box | None:
        """Reserve lobby and service stub; returns the lobby as anchor for `near: entrance`."""
        main = skeleton.main
        frame, grid = main.frame, main.grid
        program = ctx.rules.program
        street_side, service_side = ctx.params.street_side, ctx.params.service_side
        street, service = frame.local(street_side), frame.local(service_side)
        lobby = self._lobby_entry(ctx)
        anchor: Box | None = None

        if lobby is not None and skeleton.lobby_slice is not None:
            span = skeleton.lobby_slice
            rooms.append(PlannedRoom(lobby.room, frame.rect(span.u0, span.u1, 0, frame.depth)))
            end_u = 0 if street is LocalSide.U0 else frame.length - 1
            hints.append((EntranceKind.MAIN, street_side, frame.cell(end_u, frame.depth // 2)))
            anchor = frame.box(span.u0, span.u1, 0, frame.depth)
        elif lobby is not None:
            band = self._facade_band(main.bands, street)
            width = max(
                math.ceil(self._area(lobby, ctx, rng) / band.depth),
                ctx.rules.spec(lobby.room).min_side,
            )
            target = frame.length / 2 + rng.uniform(-1, 1) * frame.length / 6
            blocked = main.blocked(band, skeleton.lobby_slice, *extra.get(band.index, []))
            span = self._choose(grid, grid.round_up(width), target, blocked)
            if span is None:
                raise AllocationError("no space for the lobby")
            extra.setdefault(band.index, []).append(span)
            rooms.append(PlannedRoom(lobby.room, frame.rect(span.u0, span.u1, band.v0, band.v1)))
            v = 0 if street is LocalSide.V0 else frame.depth - 1
            hints.append((EntranceKind.MAIN, street_side, frame.cell(int(span.centre), v)))
            anchor = frame.box(span.u0, span.u1, band.v0, band.v1)
        elif (hall := next((b for b in main.bands if b.kind is BandKind.HALL), None)) is not None:
            # No lobby: customers walk straight into the hall.
            if street.is_end:
                u = 0 if street is LocalSide.U0 else frame.length - 1
                hint = frame.cell(u, (hall.v0 + hall.v1) // 2)
            else:
                v = 0 if street is LocalSide.V0 else frame.depth - 1
                hint = frame.cell(frame.length // 2, v)
            hints.append((EntranceKind.MAIN, street_side, hint))
            anchor = frame.box(0, frame.length, hall.v0, hall.v1)

        if EntranceKind.SERVICE not in ctx.rules.entrances(ctx.params):
            return anchor
        # Without corridors (small hall buildings) through a back-of-house room.
        corridors = [b for b in main.bands if b.kind is BandKind.CORRIDOR] or [
            b for b in main.bands if b.kind is BandKind.STRIP
        ]
        if service is street and anchor is not None:
            hint = hints[0][2]
        elif service.is_end:
            band = rng.choice(corridors)
            u = 0 if service is LocalSide.U0 else frame.length - 1
            hint = frame.cell(u, (band.v0 + band.v1) // 2)
            if band.kind is BandKind.STRIP:
                # A back-of-house strip ends at the door: a back room at that end.
                end = 0 if service is LocalSide.U0 else frame.length
                blocked = main.blocked(band, skeleton.lobby_slice, *extra.get(band.index, []))
                junctions = tuple(main.no_facade.get(band.index, []))
                back = self._back_room(ctx, grid, main.bands, band, end, blocked, junctions, rng)
                if back is not None and end in (back[1].u0, back[1].u1):
                    kind, span = back
                    extra.setdefault(band.index, []).append(span)
                    rooms.append(PlannedRoom(kind, frame.rect(span.u0, span.u1, band.v0, band.v1)))
        else:
            band = self._facade_band(main.bands, service)
            v = 0 if service is LocalSide.V0 else frame.depth - 1
            if band.kind in (BandKind.CORRIDOR, BandKind.HALL):
                hint = frame.cell(frame.length // 2, v)
            else:
                width = grid.round_up(program.corridor.width)
                target = skeleton.cores[0].slot.centre if skeleton.cores else frame.length / 2
                blocked = main.blocked(band, skeleton.lobby_slice, *extra.get(band.index, []))
                junctions = tuple(main.no_facade.get(band.index, []))
                # A back room through to the door, else (`service_stub`) a corridor stub.
                back = self._back_room(ctx, grid, main.bands, band, target, blocked, junctions, rng)
                kind, span = back or ("corridor", None)
                if back is None and program.service_stub:
                    span = self._choose(grid, width, target, blocked, avoid=junctions)
                if span is not None:
                    extra.setdefault(band.index, []).append(span)
                    cells = frame.rect(span.u0, span.u1, band.v0, band.v1)
                    rooms.append(PlannedRoom(kind, cells))
                    hint = frame.cell(int(span.centre), v)
                else:
                    # The back door opens into a room on that facade (openings prefer the
                    # program's service rooms: storage, loading bay, staff room, …).
                    hint = frame.cell(int(target), v)
        hints.append((EntranceKind.SERVICE, service_side, hint))
        return anchor

    def _emergency_exit(
        self,
        ctx: Context,
        skeleton: Skeleton,
        hints: list[tuple[EntranceKind, Side, Cell]],
        warnings: list[str],
    ) -> None:
        """An exit at a corridor end on a facade, else at a hall end (small hall buildings
        without a corridor), else from the stairwell, far from the others. A second
        stairwell at the end of a part has an exit of its own."""
        if EntranceKind.EMERGENCY not in ctx.rules.entrances(ctx.params):
            return
        inside = frozenset[Cell]().union(
            *(p.frame.rect(0, p.frame.length, 0, p.frame.depth) for p in skeleton.parts)
        )
        for core in skeleton.cores[1:]:
            # On the end facade beside the corridor: the stairs' landing.
            frame, stairs = core.part.frame, core.rooms[0]
            end = LocalSide.U0 if core.slot.centre < frame.length / 2 else LocalSide.U1
            u = core.slot.u0 if end is LocalSide.U0 else core.slot.u1 - 1
            cell = frame.cell(u, self._from_corridor(core.band, 1)[0])
            side = frame.side(end)
            if cell in stairs.cells and cell.neighbour(side) not in inside:
                hints.append((EntranceKind.EMERGENCY, side, cell))
        options: list[tuple[Cell, Side]] = []
        for kind in (BandKind.CORRIDOR, BandKind.HALL):
            for part in skeleton.parts:
                for band in part.bands:
                    if band.kind is not kind:
                        continue
                    for end in (LocalSide.U0, LocalSide.U1):
                        u = 0 if end is LocalSide.U0 else part.frame.length - 1
                        cell = part.frame.cell(u, (band.v0 + band.v1) // 2)
                        side = part.frame.side(end)
                        if cell.neighbour(side) not in inside:
                            options.append((cell, side))
            if options:
                break
        if not options and skeleton.core_rooms:
            for cell in sorted(skeleton.core_rooms[0].cells):
                options += [(cell, s) for s in Side if cell.neighbour(s) not in inside]
        if not options:
            warnings.append("no facade for the emergency exit")
            return
        taken = [hint for _, _, hint in hints]

        def distance(option: tuple[Cell, Side]) -> tuple[int, Cell]:
            cell = option[0]
            gaps = [abs(cell.x - t.x) + abs(cell.y - t.y) for t in taken]
            return min(gaps, default=0), cell

        cell, side = max(options, key=distance)
        hints.append((EntranceKind.EMERGENCY, side, cell))

    @staticmethod
    def _back_room(
        ctx: Context,
        grid: Grid,
        bands: list[Band],
        band: Band,
        target: float,
        blocked: list[Interval],
        junctions: tuple[Interval, ...],
        rng: random.Random,
    ) -> tuple[str, Interval] | None:
        """A full-depth back room on the service facade that the back door opens into: the
        first of the ground floor's rooms among the program's `service_rooms` (parcel drone
        bay, storeroom, staff room) whose slot isn't far too big for it. Fill rooms and
        rooms the floor always gets count; the allocator places one room fewer of the latter.
        None in a hall building with a vehicle bay, which openings prefer anyway."""
        _, role = ctx.rules.role_for(0, ctx.params)
        values = variables(ctx.params, 0)
        always = [
            e
            for e in role.rooms
            if (e.fill or (e.count is not None and e.count_range[0] > 0))
            and evaluate(e.when, values)
        ]
        hall = any(b.kind is BandKind.HALL for b in bands)
        if hall and any(ctx.rules.spec(e.room).facade_door for e in always):
            return None  # the back strip's vehicle bay takes the door anyway
        entries = {e.room: e for e in reversed(always)}
        for entry in (entries[r] for r in ctx.rules.program.service_rooms if r in entries):
            spec = ctx.rules.spec(entry.room)
            if spec.min_side > band.depth:
                continue
            low, high = entry.area or spec.area
            # A modest room: open-ended fill rooms (stockrooms) would take the whole strip.
            area = rng.randint(low, min(high, 2 * low))
            width = max(math.ceil(area / band.depth), spec.min_side)
            if width * band.depth > high * BACK_ROOM_SLACK:
                continue
            span = CorridorLayout._choose(grid, width, target, blocked, avoid=junctions)
            if span is not None:
                return entry.room, span
        return None

    # --- helpers ----------------------------------------------------------------------

    @staticmethod
    def _lobby_entry(ctx: Context) -> RoomEntry | None:
        _, role = ctx.rules.role_for(0, ctx.params)
        return next((e for e in role.rooms if e.place == "entrance"), None)

    @staticmethod
    def _area(entry: RoomEntry, ctx: Context, rng: random.Random) -> int:
        low, high = entry.area or ctx.rules.spec(entry.room).area
        return rng.randint(low, high)

    @staticmethod
    def _facade_band(bands: list[Band], side: LocalSide) -> Band:
        return bands[0] if side is LocalSide.V0 else bands[-1]


def _cramped(interval: Interval, blocked: list[Interval], length: int, min_gap: int) -> bool:
    """True if the interval leaves a gap too small for a room to a neighbour or part end."""
    neighbours = [0, length, *(b.u0 for b in blocked), *(b.u1 for b in blocked)]
    left = min((interval.u0 - n for n in neighbours if n <= interval.u0), default=0)
    right = min((n - interval.u1 for n in neighbours if n >= interval.u1), default=0)
    return 0 < left < min_gap or 0 < right < min_gap


def _min_gap(grid: Grid) -> int:
    """Smallest gap worth leaving beside a reserved slot: whole modules, a room wide."""
    return grid.round_up(MIN_GAP_CELLS)


def _shallowest(low: int) -> int:
    """Shallowest acceptable strip when a deeper one would be far too deep."""
    return max(INTERIOR_STRIP_MIN, low * 2 // 3)


def _absorb_gaps(span: Interval, others: list[Interval], length: int, min_gap: int) -> Interval:
    """Extend a forced interval over gaps too small for a room (to neighbours or part ends)."""
    left = max([0, *(o.u1 for o in others if o.u1 <= span.u0)])
    right = min([length, *(o.u0 for o in others if o.u0 >= span.u1)])
    u0 = left if span.u0 - left < min_gap else span.u0
    u1 = right if right - span.u1 < min_gap else span.u1
    return Interval(u0, u1)


def _u_range(frame: Frame, cells: list[Cell]) -> Interval:
    us = [frame.to_local(c.x + 0.5, c.y + 0.5)[0] for c in cells]
    return Interval(math.floor(min(us)), math.floor(max(us)) + 1)


def _foyer(
    frame: Frame, span: Interval, band: Band, street: LocalSide, depth: int, minimum: int
) -> frozenset[Cell]:
    """The part of a hall piece a foyer takes at the street side (across a short end, else
    along the facade), if the piece touches the street and the hall stays big enough."""
    match street:
        case LocalSide.V0 if band.v0 == 0 and band.depth - depth >= minimum:
            return frame.rect(span.u0, span.u1, band.v0, band.v0 + depth)
        case LocalSide.V1 if band.v1 == frame.depth and band.depth - depth >= minimum:
            return frame.rect(span.u0, span.u1, band.v1 - depth, band.v1)
        case LocalSide.U0 if span.u0 == 0 and span.width - depth >= minimum:
            return frame.rect(span.u0, span.u0 + depth, band.v0, band.v1)
        case LocalSide.U1 if span.u1 == frame.length and span.width - depth >= minimum:
            return frame.rect(span.u1 - depth, span.u1, band.v0, band.v1)
        case _:
            return frozenset()


def _merge_corridors(rooms: list[PlannedRoom]) -> list[PlannedRoom]:
    """Join touching corridor pieces into single rooms."""
    pieces = [r for r in rooms if r.type == "corridor"]
    others = [r for r in rooms if r.type != "corridor"]
    merged: list[set[Cell]] = []
    for piece in pieces:
        cells = set(piece.cells)
        touching = [m for m in merged if _touches(m, cells)]
        for group in touching:
            merged.remove(group)
            cells |= group
        merged.append(cells)
    return [PlannedRoom("corridor", frozenset(m)) for m in merged] + others


def _touches(a: set[Cell], b: set[Cell]) -> bool:
    small, large = (a, b) if len(a) < len(b) else (b, a)
    return any(
        Cell(c.x + dx, c.y + dy) in large
        for c in small
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
    )


def _room_index(rooms: list[PlannedRoom], cell: Cell) -> int:
    return next(i for i, r in enumerate(rooms) if cell in r.cells)
