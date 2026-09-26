"""Fill the free segments of strips with the rooms of a floor role.

A segment is a free u-interval of a strip: one long side touches a corridor, the other
the facade (or the back of another strip). Rooms become either
- full-depth slots: the room spans the strip from corridor to back, or
- cluster members: small rooms stacked along a side hallway that runs from the corridor
  into the strip (toilets, storage), so they don't turn into long thin slices.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field, replace

from roomplanner.geometry import Cell
from roomplanner.pipeline.base import AllocationError, Context, PlannedRoom
from roomplanner.pipeline.layout.frame import Band, Box, Frame, Grid, Interval, LocalSide
from roomplanner.pipeline.layout.units import subdivide
from roomplanner.rules import (
    FloorRole,
    Priority,
    RoomEntry,
    RoomSpec,
    Rules,
    WindowRule,
    evaluate,
    variables,
)

NEXT_TO_BONUS = 20  # score bonus for sharing a strip segment with a `next_to` room
SMALL_ROOM_TOLERANCE = 1.25  # a full-depth room may exceed its max area by this factor
MIN_CLUSTER_COLUMN = 4  # cells
FACADE_PENALTY = 5
FORBIDDEN_WINDOW_PENALTY = 50


@dataclass
class Segment:
    frame: Frame
    band: Band
    span: Interval
    facade: bool  # its back is an exterior wall (windows possible)
    unit: int  # slot widths are multiples of this (facade module or 1 cell)
    grid: Grid

    @property
    def box(self) -> Box:
        return self.frame.box(self.span.u0, self.span.u1, self.band.v0, self.band.v1)

    @property
    def depth(self) -> int:
        return self.band.depth

    @property
    def aligned(self) -> Interval:
        """The part of the span between grid points; partial modules go to the end slots."""
        if self.unit == 1:
            return self.span
        start, end = self.grid.ceil(self.span.u0), self.grid.floor(self.span.u1)
        return Interval(start, max(start, end))

    @property
    def capacity(self) -> int:
        return self.aligned.width // self.unit


@dataclass
class Request:
    type: str
    spec: RoomSpec
    area: int  # cells
    priority: Priority
    near: str | None
    min_area: int = 0  # > 0 for `share` rooms, which shrink towards it if space is short


@dataclass
class FullSlot:
    request: Request
    units: int
    annex: Request | None = None  # small room carved out of the slot's back corner


@dataclass
class Cluster:
    units: int
    hall: int  # hallway width in cells
    columns: int
    column_width: int
    stacks: list[list[tuple[Request, int]]]  # per column: (request, depth in cells)

    def free_depth(self, column: int, depth: int) -> int:
        return depth - sum(d for _, d in self.stacks[column])


type Slot = FullSlot | Cluster
type Option = FullSlot | Cluster | tuple[Cluster, int]  # new slot, new cluster, (cluster, column)


@dataclass
class SegmentState:
    segment: Segment
    slots: list[Slot] = field(default_factory=list[Slot])

    @property
    def free_units(self) -> int:
        return self.segment.capacity - sum(s.units for s in self.slots)


@dataclass(frozen=True)
class Anchors:
    """Where `near:` targets are, in absolute coordinates."""

    core: Box | None
    entrance: Box | None
    service: Box | None = None  # the service entrance's facade cell (ground floor)

    def get(self, near: str | None) -> Box | None:
        anchors = {"core": self.core, "entrance": self.entrance, "service": self.service}
        return anchors.get(near or "")


class Allocator:
    def __init__(
        self,
        ctx: Context,
        rules: Rules,
        segments: list[Segment],
        anchors: Anchors,
        rng: random.Random,
    ) -> None:
        self.ctx = ctx
        self.rules = rules
        self.states = [SegmentState(s) for s in segments if s.span.width > 0]
        self.anchors = anchors
        self.rng = rng
        self.hallway = rules.program.corridor.hallway_width
        self.warnings: list[str] = []
        self.units = 0
        self.level = 0
        self.fill_types: set[str] = set()
        self.fills: list[RoomEntry] = []
        self.fill_widths: dict[tuple[str, int, int], int] = {}

    # --- public -----------------------------------------------------------------------

    def allocate(self, role: FloorRole, level: int, floor_name: str) -> list[PlannedRoom]:
        self.level = level
        requests, fills = self._requests(role, level)
        self.fill_types = {e.room for e in fills}
        self.fills = fills
        queue = deque(requests)
        while queue:
            request = queue.popleft()
            placed = self._place(request)
            if not placed and request.area >= 2 * request.min_area > 0:
                # Flexible (share) rooms split in two rather than being dropped.
                request.area //= 2
                queue.appendleft(replace(request))
                queue.appendleft(request)
                continue
            if not placed:
                message = f"{floor_name}: no space for {request.priority} room {request.type}"
                if request.priority is Priority.REQUIRED:
                    raise AllocationError(message)
                warning = f"{floor_name}: dropped {request.type} (no space)"
                if warning not in self.warnings:
                    self.warnings.append(warning)
        for state in self.states:
            self._fill(state, fills)
        rooms: list[PlannedRoom] = []
        for state in self.states:
            rooms += self._materialise(state)
        return rooms

    # --- requests ---------------------------------------------------------------------

    def _requests(self, role: FloorRole, level: int) -> tuple[list[Request], list[RoomEntry]]:
        values = variables(self.ctx.params, level)
        requests: list[Request] = []
        fills: list[RoomEntry] = []
        for entry in role.rooms:
            if entry.place is not None or not evaluate(entry.when, values):
                continue
            if entry.fill:
                fills.append(entry)
                continue
            spec = self.rules.spec(entry.room)
            low, high = entry.area or spec.area
            if entry.share is not None:
                # A share counts against the area the room may use (facades if it needs windows).
                target = entry.share * sum(
                    s.segment.span.width * s.segment.depth
                    for s in self.states
                    if spec.windows is not WindowRule.REQUIRED or s.segment.facade
                )
                count = max(1, math.ceil(target / high))
                area = max(low, round(target / count))
                requests += [
                    Request(entry.room, spec, area, entry.priority, entry.near, min_area=low)
                    for _ in range(count)
                ]
            else:
                count = self.rng.randint(*entry.count_range)
                requests += [
                    Request(
                        entry.room, spec, self.rng.randint(low, high), entry.priority, entry.near
                    )
                    for _ in range(count)
                ]
        # Fixed counts before flexible shares, rooms needing windows first, big before small.
        order = {Priority.REQUIRED: 0, Priority.NORMAL: 1, Priority.OPTIONAL: 2}
        requests.sort(
            key=lambda r: (
                order[r.priority],
                r.min_area > 0,
                r.spec.windows is not WindowRule.REQUIRED,
                -r.area,
            )
        )
        if not fills:
            raise AllocationError("floor role has no fill room")
        return requests, fills

    # --- placement --------------------------------------------------------------------

    def _min_width(self, spec: RoomSpec, depth: int) -> int:
        return max(spec.min_side, math.ceil(depth / spec.max_aspect))

    def _is_small(self, request: Request, segment: Segment) -> bool:
        width = self._min_width(request.spec, segment.depth)
        width = math.ceil(width / segment.unit) * segment.unit
        max_area = request.spec.area[1]
        return width * segment.depth > max_area * SMALL_ROOM_TOLERANCE

    def _full_units(self, request: Request, segment: Segment) -> int:
        width = self._min_width(request.spec, segment.depth)
        if request.spec.vestibule is not None:  # the vestibule is carved out of the slot
            width += self.rules.spec(request.spec.vestibule).min_side
        minimum = math.ceil(width / segment.unit)
        return max(minimum, round(request.area / segment.depth / segment.unit))

    def _place(self, request: Request) -> bool:
        if (
            request.spec.cluster
            and request.spec.access
            and self.rng.random() < request.spec.annex
            and self._place_annex(request)
        ):
            return True
        options: list[tuple[float, float, SegmentState, Option]] = []
        for state in self.states:
            segment = state.segment
            if request.spec.windows is WindowRule.REQUIRED and not segment.facade:
                continue
            option = self._option(request, state)
            if option is None:
                continue
            options.append((self._score(request, segment), self.rng.random(), state, option))
        if not options:
            return False
        _, _, state, option = min(options, key=lambda o: (o[0], o[1]))
        match option:
            case (cluster, column):
                cluster.stacks[column].append((request, self._member_depth(request, cluster)))
            case Cluster() as cluster:
                cluster.stacks[0].append((request, self._member_depth(request, cluster)))
                state.slots.append(cluster)
            case FullSlot() as slot:
                state.slots.append(slot)
        return True

    # --- annexes ----------------------------------------------------------------------

    def _annex_size(
        self, host: RoomSpec, annex: Request, width: int, depth: int
    ) -> tuple[int, int] | None:
        """(width, depth) of an annex in the back corner of a host slot, if it fits.

        The host keeps its minimum side in front of and beside the annex.
        """
        low = annex.spec.min_side
        max_w, max_d = width - host.min_side, depth - host.min_side
        if max_w < low or max_d < low:
            return None
        annex_d = min(max_d, max(low, round(math.sqrt(annex.area))))
        annex_w = min(max_w, max(low, math.ceil(annex.area / annex_d)))
        return annex_w, annex_d

    def _place_annex(self, request: Request) -> bool:
        """Carve the room out of an existing host slot, or add a new host (a fill type)."""
        hosts = [t for t in request.spec.access if t not in self.rules.program.units]
        options: list[tuple[float, float, SegmentState, FullSlot, bool]] = []
        for state in self.states:
            segment = state.segment
            for slot in state.slots:
                if not isinstance(slot, FullSlot) or slot.annex is not None:
                    continue
                if slot.request.type not in hosts:
                    continue
                width = slot.units * segment.unit
                if self._annex_size(slot.request.spec, request, width, segment.depth):
                    options.append(
                        (self._score(request, segment), self.rng.random(), state, slot, False)
                    )
            for host in hosts:
                if host not in self.fill_types:
                    continue
                spec = self.rules.spec(host)
                if spec.windows is WindowRule.REQUIRED and not segment.facade:
                    continue
                if spec.min_side > segment.depth:
                    continue
                low, high = spec.area
                host_request = Request(
                    host, spec, self.rng.randint(low, high), Priority.OPTIONAL, None
                )
                units = self._full_units(host_request, segment)
                needed = spec.min_side + request.spec.min_side
                units = max(units, math.ceil(needed / segment.unit))
                if units > state.free_units:
                    continue
                if not self._annex_size(spec, request, units * segment.unit, segment.depth):
                    continue
                slot = FullSlot(host_request, units)
                options.append(
                    (self._score(request, segment), self.rng.random(), state, slot, True)
                )
        if not options:
            return False
        # Existing hosts first, then the best segment.
        _, _, state, slot, new = min(options, key=lambda o: (o[4], o[0], o[1]))
        slot.annex = request
        if new:
            state.slots.append(slot)
        return True

    def _option(self, request: Request, state: SegmentState) -> Option | None:
        """How the request could go into this segment, without changing anything yet."""
        segment = state.segment
        min_side = request.spec.min_side
        if min_side > segment.depth:
            return None
        full = FullSlot(request, self._full_units(request, segment))
        # Cluster members mostly don't touch the facade, so rooms needing windows stay full.
        needs_window = request.spec.windows is WindowRule.REQUIRED
        low, high = request.spec.area
        if (column := self._facade_column(request.spec, low, high, segment)) is not None:
            hall = math.ceil(self.hallway / segment.unit) * segment.unit
            cluster = Cluster((hall + column) // segment.unit, hall, 1, column, [[]])
            front = self._member_depth(request, cluster)
            backs = self._backs(segment, column, front)
            fits = front <= math.ceil(high / column) and cluster.units <= state.free_units
            if backs is not None and fits:
                cluster.stacks[0] = backs
                return cluster
        if needs_window or not (request.spec.cluster or self._is_small(request, segment)):
            return full if full.units <= state.free_units else None
        for slot in state.slots:
            if isinstance(slot, Cluster) and slot.column_width >= min_side:
                for column in range(slot.columns):
                    if self._member_depth(request, slot) <= slot.free_depth(column, segment.depth):
                        return slot, column
        cluster = self._new_cluster(request, segment)
        if (
            cluster.units <= state.free_units
            and self._member_depth(request, cluster) <= segment.depth
        ):
            return cluster
        return full if full.units <= state.free_units else None

    def _new_cluster(self, request: Request, segment: Segment) -> Cluster:
        """Hallway plus one or two columns, each a whole number of units (facade grid)."""
        unit = segment.unit
        hall = math.ceil(self.hallway / unit) * unit
        column = math.ceil(max(MIN_CLUSTER_COLUMN, request.spec.min_side) / unit) * unit
        units = (hall + column) // unit
        columns = 1
        column_width = column
        return Cluster(units, hall, columns, column_width, [[] for _ in range(columns)])

    def _member_depth(self, request: Request, cluster: Cluster) -> int:
        return max(request.spec.min_side, math.ceil(request.area / cluster.column_width))

    def _score(self, request: Request, segment: Segment) -> float:
        score = 0.0
        if (anchor := self.anchors.get(request.near)) is not None:
            score += segment.box.gap(anchor)
        if segment.facade:
            if request.spec.windows is WindowRule.FORBIDDEN:
                score += FORBIDDEN_WINDOW_PENALTY
            elif request.spec.windows is WindowRule.OPTIONAL:
                score += FACADE_PENALTY
        if request.spec.next_to:
            gaps = [
                -NEXT_TO_BONUS if state.segment is segment else state.segment.box.gap(segment.box)
                for state in self.states
                if any(t in request.spec.next_to for t in _slot_types(state))
            ]
            score += min(gaps, default=0)
        return score

    # --- fill -------------------------------------------------------------------------

    def _fill_entries(self, segment: Segment, fills: list[RoomEntry]) -> list[RoomEntry]:
        """Facades get rooms that need windows if possible; interiors only rooms that don't."""
        fills = [e for e in fills if self.rules.spec(e.room).min_side <= segment.depth]
        needs_window = [e for e in fills if self.rules.spec(e.room).windows is WindowRule.REQUIRED]
        others = [e for e in fills if e not in needs_window]
        filler = [RoomEntry(room=self.rules.program.cluster_filler, fill=True)]
        if segment.facade:
            return needs_window or others or filler
        return others or filler

    def _fill(self, state: SegmentState, all_fills: list[RoomEntry]) -> None:
        segment = state.segment
        fills = self._fill_entries(segment, all_fills)
        if not state.slots and state.free_units == 0:
            # Segment narrower than one module: a single room takes the partial modules.
            width = segment.span.width
            fitting = [e for e in fills if self._min_width(self.rules.spec(e.room), 0) <= width]
            filler = RoomEntry(room=self.rules.program.cluster_filler, fill=True)
            state.slots.append(self._fill_slot((fitting or [filler])[0], 0, segment))
            return
        while state.free_units > 0:
            fitting = [e for e in fills if self._fill_min_units(e, segment) <= state.free_units]
            if not fitting and not state.slots:
                # Narrow leftover: the cluster filler (storage) is the smallest room there is.
                fitting = [RoomEntry(room=self.rules.program.cluster_filler, fill=True)]
            entry = self.rng.choice(fitting or fills)
            spec = self.rules.spec(entry.room)
            stackable = spec.cluster or spec.windows is not WindowRule.REQUIRED
            if stackable and (cluster := self._fill_cluster(entry, state)) is not None:
                # Small fill rooms (coffins, …) in deep strips: stacked along a hallway.
                state.slots.append(cluster)
                continue
            if (cluster := self._facade_cluster(entry, state)) is not None:
                # Small window rooms in deep facade strips: at the facade, back rooms behind.
                state.slots.append(cluster)
                continue
            min_units = self._fill_min_units(entry, segment)
            units = self._uniform_units(entry, segment, min_units)
            remaining = state.free_units
            if remaining < min_units and state.slots:
                self._spread(state, remaining, fills)
                return
            if remaining - units < min_units:
                # Room for this one, but not for another: the rest is spread over the row.
                state.slots.append(self._fill_slot(entry, min(units, remaining), segment))
                if remaining > units:
                    self._spread(state, remaining - units, fills)
                return
            state.slots.append(self._fill_slot(entry, units, segment))

    def _uniform_units(self, entry: RoomEntry, segment: Segment, min_units: int) -> int:
        """One width per fill room type and strip depth on a floor: rooms line up in a grid."""
        key = (entry.room, segment.depth, segment.unit)
        if key not in self.fill_widths:
            low, high = entry.area or self.rules.spec(entry.room).area
            target = (low + high) / 2 / segment.depth / segment.unit
            self.fill_widths[key] = max(min_units, round(target))
        return self.fill_widths[key]

    def _spread(self, state: SegmentState, extra: int, fills: list[RoomEntry]) -> None:
        """Widen the row's rooms by one unit each, in turn, preferring fill rooms."""
        full = [s for s in state.slots if isinstance(s, FullSlot)]
        wanted = {e.room for e in fills}
        preferred = [s for s in full if s.request.type in wanted] or [
            s for s in full if not s.request.spec.cluster
        ]
        targets = preferred or full or [state.slots[-1]]
        for i in range(extra):
            targets[i % len(targets)].units += 1

    def _fill_cluster(self, entry: RoomEntry, state: SegmentState) -> Cluster | None:
        """A hallway with one or two columns of stacked fill rooms, if full depth is too big."""
        segment = state.segment
        spec = self.rules.spec(entry.room)
        low, high = entry.area or spec.area
        probe = Request(entry.room, spec, high, Priority.OPTIONAL, None)
        if not self._is_small(probe, segment):
            return None
        unit = segment.unit
        hall = math.ceil(self.hallway / unit) * unit
        column = math.ceil(max(MIN_CLUSTER_COLUMN, spec.min_side) / unit) * unit
        for columns in (2, 1):
            units = (hall + columns * column) // unit
            if units <= state.free_units:
                break
        else:
            return None
        depth = max(spec.min_side, round((low + high) / 2 / column))
        count = max(1, segment.depth // depth)
        stacks = [
            [(Request(entry.room, spec, depth * column, Priority.OPTIONAL, None), depth)] * count
            for _ in range(columns)
        ]
        return Cluster(units, hall, columns, column, stacks)

    def _facade_cluster(self, entry: RoomEntry, state: SegmentState) -> Cluster | None:
        """Fill rooms needing windows in deep facade strips: two columns of facade stacks."""
        spec = self.rules.spec(entry.room)
        low, high = entry.area or spec.area
        column = self._facade_column(spec, low, high, state.segment)
        if column is None:
            return None
        front = min(high // column, max(spec.min_side, round((low + high) / 2 / column)))
        stack = self._backs(state.segment, column, front)
        if stack is None:
            return None
        stack.append((Request(entry.room, spec, front * column, Priority.OPTIONAL, None), front))
        hall = math.ceil(self.hallway / state.segment.unit) * state.segment.unit
        for columns in (2, 1):
            units = (hall + columns * column) // state.segment.unit
            if units <= state.free_units:
                return Cluster(units, hall, columns, column, [list(stack) for _ in range(columns)])
        return None

    def _facade_column(self, spec: RoomSpec, low: int, high: int, segment: Segment) -> int | None:
        """Column width of a facade stack if a room needing windows is too small for the strip.

        Such rooms (exam rooms, offices) would be far too big spanning a deep strip; instead a
        hallway leads to the facade, the room sits at its end and windowless rooms behind it.
        """
        if not segment.facade or spec.windows is not WindowRule.REQUIRED or spec.cluster:
            return None
        probe = Request("probe", spec, high, Priority.OPTIONAL, None)
        if not self._is_small(probe, segment):
            return None
        unit = segment.unit
        back_min = self.rules.spec(self._back_entry().room).min_side
        smallest = max(MIN_CLUSTER_COLUMN, spec.min_side, back_min)
        column = max(smallest, round(math.sqrt((low + high) / 2) / unit) * unit)
        return math.ceil(column / unit) * unit

    def _back_entry(self) -> RoomEntry:
        """The room behind facade stacks: the floor's first windowless fill room, or storage."""
        for entry in self.fills:
            spec = self.rules.spec(entry.room)
            if spec.windows is not WindowRule.REQUIRED and not spec.circulation:
                return entry
        return RoomEntry(room=self.rules.program.cluster_filler, fill=True)

    def _backs(self, segment: Segment, column: int, front: int) -> list[tuple[Request, int]] | None:
        """Back rooms filling the strip from the corridor up to a facade room `front` deep."""
        back = self._back_entry()
        spec = self.rules.spec(back.room)
        rest = segment.depth - front
        if rest < spec.min_side:
            return None
        high = (back.area or spec.area)[1]
        count = max(1, min(rest // spec.min_side, math.ceil(rest * column / high)))
        depths = [rest // count] * count
        depths[-1] += rest - sum(depths)
        return [(Request(back.room, spec, d * column, Priority.OPTIONAL, None), d) for d in depths]

    def _fill_min_units(self, entry: RoomEntry, segment: Segment) -> int:
        spec = self.rules.spec(entry.room)
        width = self._min_width(spec, segment.depth)
        if spec.vestibule is not None:
            width += self.rules.spec(spec.vestibule).min_side
        return math.ceil(width / segment.unit)

    def _fill_slot(self, entry: RoomEntry, units: int, segment: Segment) -> FullSlot:
        area = units * segment.unit * segment.depth
        request = Request(entry.room, self.rules.spec(entry.room), area, Priority.OPTIONAL, None)
        return FullSlot(request, units)

    # --- materialisation --------------------------------------------------------------

    def _materialise(self, state: SegmentState) -> list[PlannedRoom]:
        segment = state.segment
        if not state.slots:
            return []
        slots = sorted(state.slots, key=lambda s: (self._pull(s, segment), self.rng.random()))
        slots = _neighbours_together(slots)
        widths = [slot.units * segment.unit for slot in slots]
        # Partial modules at the building ends go to the outermost slots.
        widths[0] += segment.aligned.u0 - segment.span.u0
        widths[-1] += segment.span.u1 - segment.aligned.u1
        rooms: list[PlannedRoom] = []
        position = segment.span.u0
        for slot, width in zip(slots, widths, strict=True):
            span = Interval(position, position + width)
            if isinstance(slot, FullSlot) and slot.request.type in self.rules.program.units:
                self.units += 1
                unit = f"{self.level}-{self.units:02d}"
                spec = self.rules.program.units[slot.request.type]
                rooms += subdivide(
                    segment.frame, segment.band, span, unit, spec, self.rules, self.rng
                )
            elif isinstance(slot, FullSlot) and slot.request.spec.vestibule is not None:
                rooms += self._with_vestibule(slot, segment, span, slots, position)
            elif isinstance(slot, FullSlot) and slot.annex is not None:
                rooms += self._with_annex(slot, slot.annex, segment, span)
            elif isinstance(slot, FullSlot):
                rooms.append(PlannedRoom(slot.request.type, self._rect(segment, span)))
            else:
                rooms += self._cluster_rooms(slot, segment, span)
            position = span.u1
        return rooms

    def _with_annex(
        self, slot: FullSlot, annex: Request, segment: Segment, span: Interval
    ) -> list[PlannedRoom]:
        """Host slot with the annex in a back corner (away from the corridor)."""
        band = segment.band
        size = self._annex_size(slot.request.spec, annex, span.width, band.depth)
        if size is None:  # partial modules made it narrower than planned; keep the host
            return [PlannedRoom(slot.request.type, self._rect(segment, span))]
        width, depth = size
        u0 = span.u0 if self.rng.random() < 0.5 else span.u1 - width
        if band.corridor_at is LocalSide.V0:
            v0, v1 = band.v1 - depth, band.v1
        else:
            v0, v1 = band.v0, band.v0 + depth
        annex_cells = segment.frame.rect(u0, u0 + width, v0, v1)
        host = PlannedRoom(slot.request.type, self._rect(segment, span) - annex_cells)
        return [host, PlannedRoom(annex.type, annex_cells, host=host)]

    def _with_vestibule(
        self,
        slot: FullSlot,
        segment: Segment,
        span: Interval,
        slots: list[Slot],
        position: int,
    ) -> list[PlannedRoom]:
        """A full-depth vestibule column beside the room; the room is entered only through it."""
        spec = slot.request.spec
        assert spec.vestibule is not None
        width = self.rules.spec(spec.vestibule).min_side
        if span.width - width < spec.min_side:
            return [PlannedRoom(slot.request.type, self._rect(segment, span))]
        # Put the vestibule on the side away from a `next_to` neighbour (recovery beside OR).
        index = next(i for i, s in enumerate(slots) if s is slot)
        before = [_first_type(s) for s in slots[:index]]
        after = [_first_type(s) for s in slots[index + 1 :]]
        wanted = set(self.rules.spec(spec.vestibule).next_to) | {
            t for t in (*before, *after) if slot.request.type in self._next_to(t)
        }
        at_start = not (before and before[-1] in wanted)
        vest = (
            Interval(span.u0, span.u0 + width) if at_start else Interval(span.u1 - width, span.u1)
        )
        room = Interval(vest.u1, span.u1) if at_start else Interval(span.u0, vest.u0)
        vestibule = PlannedRoom(spec.vestibule, self._rect(segment, vest))
        return [
            vestibule,
            PlannedRoom(slot.request.type, self._rect(segment, room), host=vestibule),
        ]

    def _next_to(self, room_type: str | None) -> list[str]:
        return self.rules.spec(room_type).next_to if room_type else []

    def _pull(self, slot: Slot, segment: Segment) -> int:
        """-1 to sort towards u0, +1 towards u1, 0 anywhere."""
        request = (
            slot.request
            if isinstance(slot, FullSlot)
            else next((r for stack in slot.stacks for r, _ in stack), None)
        )
        if request is None or (anchor := self.anchors.get(request.near)) is None:
            return 0
        anchor_u, _ = segment.frame.to_local(*anchor.centre)
        return -1 if anchor_u < segment.span.centre else 1

    @staticmethod
    def _rect(segment: Segment, span: Interval) -> frozenset[Cell]:
        return segment.frame.rect(span.u0, span.u1, segment.band.v0, segment.band.v1)

    def _cluster_rooms(
        self, cluster: Cluster, segment: Segment, span: Interval
    ) -> list[PlannedRoom]:
        band = segment.band
        rooms: list[PlannedRoom] = []
        # Extra width (partial modules, widened leftovers): a room of its own at the start if
        # it is wide enough, else a slightly wider hallway; the cluster rooms keep their size.
        column_width = cluster.column_width
        filler = self.rules.program.cluster_filler
        filler_min = self.rules.spec(filler).min_side
        extra = span.width - cluster.hall - cluster.columns * column_width
        if extra >= filler_min:
            rooms.append(
                PlannedRoom(filler, self._rect(segment, Interval(span.u0, span.u0 + extra)))
            )
            span = Interval(span.u0 + extra, span.u1)
        if cluster.columns == 2:
            columns = [
                Interval(span.u0, span.u0 + column_width),
                Interval(span.u1 - column_width, span.u1),
            ]
            hallway = Interval(columns[0].u1, columns[1].u0)
        else:
            hallway = Interval(span.u0, span.u1 - column_width)
            columns = [Interval(hallway.u1, span.u1)]
        rooms.append(PlannedRoom("corridor", self._rect(segment, hallway)))

        from_v0 = band.corridor_at is LocalSide.V0
        for column, stack in zip(columns, cluster.stacks, strict=True):
            depths = [d for _, d in stack]
            types = [r.type for r, _ in stack]
            rest = band.depth - sum(depths)
            if rest >= filler_min or not stack:
                types.append(filler)
                depths.append(rest)
            else:
                depths[-1] += rest
            offset = 0
            for room_type, depth in zip(types, depths, strict=True):
                if from_v0:
                    v0, v1 = band.v0 + offset, band.v0 + offset + depth
                else:
                    v0, v1 = band.v1 - offset - depth, band.v1 - offset
                cells = segment.frame.rect(column.u0, column.u1, v0, v1)
                rooms.append(PlannedRoom(room_type, cells))
                offset += depth
        return rooms


def _first_type(slot: Slot) -> str | None:
    if isinstance(slot, FullSlot):
        return slot.request.type
    return next((r.type for stack in slot.stacks for r, _ in stack), None)


def _slot_types(state: SegmentState) -> set[str]:
    return {t for s in state.slots if (t := _first_type(s)) is not None}


def _neighbours_together(slots: list[Slot]) -> list[Slot]:
    """Move each slot with `next_to` types right after the first slot of such a type."""
    ordered = list(slots)
    for slot in slots:
        if not isinstance(slot, FullSlot) or not slot.request.spec.next_to:
            continue
        target = next(
            (s for s in ordered if s is not slot and _first_type(s) in slot.request.spec.next_to),
            None,
        )
        if target is None:
            continue
        ordered.remove(slot)
        ordered.insert(ordered.index(target) + 1, slot)
    return ordered
