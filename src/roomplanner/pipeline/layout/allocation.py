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

    def get(self, near: str | None) -> Box | None:
        return {"core": self.core, "entrance": self.entrance}.get(near or "")


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

    # --- public -----------------------------------------------------------------------

    def allocate(self, role: FloorRole, level: int, floor_name: str) -> list[PlannedRoom]:
        requests, fills = self._requests(role, level)
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
        minimum = math.ceil(self._min_width(request.spec, segment.depth) / segment.unit)
        return max(minimum, round(request.area / segment.depth / segment.unit))

    def _place(self, request: Request) -> bool:
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

    def _option(self, request: Request, state: SegmentState) -> Option | None:
        """How the request could go into this segment, without changing anything yet."""
        segment = state.segment
        min_side = request.spec.min_side
        if min_side > segment.depth:
            return None
        full = FullSlot(request, self._full_units(request, segment))
        if not self._is_small(request, segment):
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
        return score

    # --- fill -------------------------------------------------------------------------

    def _fill_entries(self, segment: Segment, fills: list[RoomEntry]) -> list[RoomEntry]:
        """Facades get rooms that need windows if possible; interiors only rooms that don't."""
        needs_window = [e for e in fills if self.rules.spec(e.room).windows is WindowRule.REQUIRED]
        others = [e for e in fills if e not in needs_window]
        if segment.facade:
            return needs_window or others
        return others or [RoomEntry(room=self.rules.program.cluster_filler, fill=True)]

    def _fill(self, state: SegmentState, all_fills: list[RoomEntry]) -> None:
        segment = state.segment
        fills = self._fill_entries(segment, all_fills)
        if not state.slots and state.free_units == 0:
            # Segment narrower than one module: a single room takes the partial modules.
            state.slots.append(self._fill_slot(fills[0], 0, segment))
            return
        while state.free_units > 0:
            fitting = [e for e in fills if self._fill_min_units(e, segment) <= state.free_units]
            if not fitting and not state.slots:
                # Narrow leftover: the cluster filler (storage) is the smallest room there is.
                fitting = [RoomEntry(room=self.rules.program.cluster_filler, fill=True)]
            entry = self.rng.choice(fitting or fills)
            spec = self.rules.spec(entry.room)
            min_units = self._fill_min_units(entry, segment)
            low, high = entry.area or spec.area
            units = max(
                min_units, round(self.rng.randint(low, high) / segment.depth / segment.unit)
            )
            remaining = state.free_units
            if remaining < min_units and state.slots:
                # Too little left for another room: widen an existing one instead.
                full = [s for s in state.slots if isinstance(s, FullSlot)]
                target = self.rng.choice(full) if full else state.slots[-1]
                target.units += remaining
                return
            if remaining - units < min_units:
                units = remaining
            state.slots.append(self._fill_slot(entry, units, segment))

    def _fill_min_units(self, entry: RoomEntry, segment: Segment) -> int:
        return math.ceil(self._min_width(self.rules.spec(entry.room), segment.depth) / segment.unit)

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
        widths = [slot.units * segment.unit for slot in slots]
        # Partial modules at the building ends go to the outermost slots.
        widths[0] += segment.aligned.u0 - segment.span.u0
        widths[-1] += segment.span.u1 - segment.aligned.u1
        rooms: list[PlannedRoom] = []
        position = segment.span.u0
        for slot, width in zip(slots, widths, strict=True):
            span = Interval(position, position + width)
            if isinstance(slot, FullSlot):
                rooms.append(PlannedRoom(slot.request.type, self._rect(segment, span)))
            else:
                rooms += self._cluster_rooms(slot, segment, span)
            position = span.u1
        return rooms

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
        hall = cluster.hall
        width = span.width
        column_width = (width - hall) // cluster.columns
        if cluster.columns == 2:
            columns = [
                Interval(span.u0, span.u0 + column_width),
                Interval(span.u1 - (width - hall - column_width), span.u1),
            ]
            hallway = Interval(columns[0].u1, columns[1].u0)
        else:
            hallway = Interval(span.u0, span.u0 + hall)
            columns = [Interval(hallway.u1, span.u1)]
        rooms.append(PlannedRoom("corridor", self._rect(segment, hallway)))

        from_v0 = band.corridor_at is LocalSide.V0
        filler = self.rules.program.cluster_filler
        filler_min = self.rules.spec(filler).min_side
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
