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
from collections import Counter, deque
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from roomplanner.geometry import Cell, Side
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
ABSORB_TOLERANCE = 1.4  # leftovers may grow a room this far before a storeroom takes them
MAX_SLIVER = 4  # cells: narrower leftovers widen the rooms beside them if they can
SLIVER_LIMIT = 1.45  # ... as far as this factor of their maximum area
MIN_CLUSTER_COLUMN = 4  # cells
FACADE_PENALTY = 5
FORBIDDEN_WINDOW_PENALTY = 50
FRAGMENT_PENALTY = 30  # a placement leaving a rest no fill room fits


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


REQUIRED_SHARE = 0.75  # of the segments' area that required rooms may take together


@dataclass
class Request:
    type: str
    spec: RoomSpec
    area: int  # cells
    priority: Priority
    near: str | None
    min_area: int = 0  # > 0 for `share` rooms, which shrink towards it if space is short
    leftover: bool = False  # the cluster filler taking space no other room fits
    requires: tuple[str, ...] = ()  # only if the floor gets one of these rooms


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
    lobby: str | None = None  # room type at `entrance` (the lobby rooms in `access` open onto)

    def get(self, near: str | None) -> Box | None:
        anchors = {"core": self.core, "entrance": self.entrance, "service": self.service}
        return anchors.get(near or "")

    def opens_onto_lobby(self, spec: RoomSpec) -> bool:
        return self.entrance is not None and self.lobby in spec.access


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
        self.fills: list[RoomEntry] = []  # all fill rooms, leftover ones included
        self.main_fills: list[RoomEntry] = []
        self.pending: deque[Request] = deque()  # requests not placed yet
        self.fill_widths: dict[tuple[str, int, int], int] = {}
        self.fill_counts: dict[str, int] = {}  # fill rooms picked on this floor, by type

    # --- public -----------------------------------------------------------------------

    def allocate(
        self,
        role: FloorRole,
        level: int,
        floor_name: str,
        behind: list[tuple[Segment, frozenset[Cell]]] | None = None,
        existing: Iterable[str] = (),
        reserved: Iterable[str] = (),
    ) -> list[PlannedRoom]:
        """Rooms of the floor's segments; `behind`: rectangles behind the core, which take
        rooms that don't fit elsewhere (required ones first), else the floor's `core_back`
        room, else fill rooms. `existing`: types already on the floor, which count against
        the fill rooms' `limit`. `reserved`: rooms the layout placed already (the back room),
        each standing in for one counted room of its type."""
        self.level = level
        self.core_back = role.core_back or self.rules.program.core_back
        self.floor_name = floor_name
        self.dropped: list[Request] = []
        requests, fills = self._requests(role, level)
        for room in reserved:
            if (request := next((r for r in requests if r.type == room), None)) is not None:
                requests.remove(request)
        self._squeeze(requests)
        self.fill_types = {e.room for e in fills}
        self.fills = fills
        self.fill_counts = dict(Counter(existing))
        # Optional fill rooms only take leftovers the others don't fit (studios beside flats).
        self.main_fills = [e for e in fills if e.priority is not Priority.OPTIONAL] or fills
        queue = deque(requests)
        self.pending = queue
        while queue:
            request = queue.popleft()
            placed = self._place(request)
            if placed and (partner := self._partner(request)) is not None:
                # Its partner goes next, while there is still space beside it.
                queue.remove(partner)
                queue.appendleft(partner)
            if not placed and request.area >= 2 * request.min_area > 0:
                # Flexible (share) rooms split in two rather than being dropped.
                request.area //= 2
                queue.appendleft(replace(request))
                queue.appendleft(request)
                continue
            if not placed:
                self.dropped.append(request)
                warning = f"{floor_name}: dropped {request.type} (no space)"
                # Optional rooms are nice to have: only where there is space, no warning.
                if request.priority is Priority.NORMAL and warning not in self.warnings:
                    self.warnings.append(warning)
        for state in self.states:
            self._fill(state, self.main_fills)
        rooms: list[PlannedRoom] = []
        for state in self.states:
            rooms += self._materialise(state)
        self.dropped.sort(key=lambda r: r.priority is not Priority.REQUIRED)
        for segment, cells in behind or []:
            if not self._join_unit(cells, rooms):
                rooms += self.rooms_behind(segment, cells)
        for request in self.dropped:
            if request.priority is Priority.REQUIRED:
                message = f"{floor_name}: no space for {request.priority} room {request.type}"
                raise AllocationError(message)
        return rooms

    def _squeeze(self, requests: list[Request]) -> None:
        """Required rooms that picked large areas together take more than the segments hold
        (a casino's cage): they shrink towards their least area, so all of them still fit."""
        room = sum(s.segment.span.width * s.segment.depth for s in self.states)
        required = [r for r in requests if r.priority is Priority.REQUIRED]
        excess = sum(r.area for r in required) - REQUIRED_SHARE * room
        slack = sum(r.area - r.spec.area[0] for r in required if r.area > r.spec.area[0])
        if excess <= 0 or slack <= 0:
            return
        keep = 1 - min(1.0, excess / slack)
        for r in required:
            if r.area > r.spec.area[0]:
                r.area = r.spec.area[0] + round((r.area - r.spec.area[0]) * keep)

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
                        entry.room,
                        spec,
                        self.rng.randint(low, high),
                        entry.priority,
                        entry.near,
                        requires=tuple(entry.requires),
                    )
                    for _ in range(count)
                ]
        # An observation room only where there is an interview room.
        types = {r.type for r in requests}
        requests = [r for r in requests if not r.requires or types & set(r.requires)]
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
            score = (
                self._score(request, segment)
                + self._lobby_side(request, state)
                + self._partner_room(request, state, option)
                + self._fragments(request, state, option)
            )
            options.append((score, self.rng.random(), state, option))
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
                if host not in self.fill_types or any(
                    self._at_limit(e) for e in self.fills if e.room == host
                ):
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
            self.fill_counts[slot.request.type] = self.fill_counts.get(slot.request.type, 0) + 1
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
        needs_window = needs_window or request.spec.facade_door is not None  # a vehicle door
        low, high = request.spec.area
        if (column := self._facade_column(request.spec, low, high, segment)) is not None:
            hall = math.ceil(self.hallway / segment.unit) * segment.unit
            cluster = Cluster((hall + column) // segment.unit, hall, 1, column, [[]])
            front = self._member_depth(request, cluster)
            backs = self._backs(segment, column, front)
            # Only with room to spare: the hallway costs width a full slot would not.
            spare = cluster.units + self._full_units(request, segment) <= state.free_units
            fits = front <= math.ceil(high / column) and spare
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
        rest = segment.depth - self._member_depth(request, cluster)
        # No room could stack behind it: no hallway needed, it opens onto the corridor.
        if (
            cluster.units <= state.free_units
            and rest >= self.rules.spec(self.rules.program.cluster_filler).min_side
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

    def _lobby_side(self, request: Request, state: SegmentState) -> float:
        """Bonus for a room opening onto the lobby (`access`) where it can still go beside it:
        the segment shares a side with the lobby and no such room took that end yet."""
        lobby = self.anchors.entrance
        if lobby is None or not self.anchors.opens_onto_lobby(request.spec):
            return 0.0
        if not _share_side(state.segment.box, lobby):
            return 0.0
        taken = any(
            self.anchors.opens_onto_lobby(r.spec)
            for slot in state.slots
            for r in _slot_requests(slot)
        )
        return 0.0 if taken else -NEXT_TO_BONUS

    def _partner(self, request: Request) -> Request | None:
        """The first pending room that wants to be `next_to` the placed one and has no such
        room yet (an interview room's observation room)."""
        placed = Counter(
            r.type for s in self.states for slot in s.slots for r in _slot_requests(slot)
        )
        for pending in self.pending:
            if request.type not in pending.spec.next_to or pending.type == request.type:
                continue
            # One partner per placed room: the second observation room waits for a second
            # interview room.
            partners = sum(placed[t] for t in pending.spec.next_to)
            if placed[pending.type] < partners:
                return pending
        return None

    def _partner_room(self, request: Request, state: SegmentState, option: Option) -> float:
        """Penalty if rooms still to come that want to be `next_to` this one, or a room already
        in the segment, won't fit there any more (bigger rooms are placed first and would
        take the space an observation room needs beside its interview room)."""
        types = {request.type} | _slot_types(state)
        partners = [
            r for r in self.pending if r.type != request.type and types & set(r.spec.next_to)
        ]
        if not partners:
            return 0.0
        used = option.units if isinstance(option, FullSlot | Cluster) else 0
        needed = sum(self._full_units(r, state.segment) for r in partners)
        return 0.0 if used + needed <= state.free_units else NEXT_TO_BONUS

    def _fragments(self, request: Request, state: SegmentState, option: Option) -> float:
        """Penalty if the option leaves a rest too narrow for the floor's fill rooms.

        Neighbours (`next_to`, the lobby) matter more: rooms with such relations don't pay it.
        """
        if not isinstance(option, FullSlot | Cluster):
            return 0.0  # joins a cluster: no width used
        if request.spec.next_to or any(request.type in r.spec.next_to for r in self.pending):
            return 0.0
        if self.anchors.opens_onto_lobby(request.spec):
            return 0.0
        segment = state.segment
        widths = [
            self._fill_min_units(e, segment)
            for e in self._fill_entries(segment, self.main_fills)
            if e.room != self.rules.program.cluster_filler
        ]
        if not widths or state.free_units < min(widths):
            return 0.0  # no fill room fits anyway
        rest = state.free_units - option.units
        return FRAGMENT_PENALTY if 0 < rest < min(widths) else 0.0

    def _score(self, request: Request, segment: Segment) -> float:
        score = 0.0
        if (anchor := self.anchors.get(request.near)) is not None:
            score += segment.box.gap(anchor)
        partners = [s for s in self.states if self._wanted_beside(request, s)]
        # Beside its partner a windowless room may take the facade (sterilization by the OR).
        beside = any(s.segment is segment for s in partners)
        if segment.facade and not beside:
            if request.spec.windows is WindowRule.FORBIDDEN:
                score += FORBIDDEN_WINDOW_PENALTY
            elif request.spec.windows is WindowRule.OPTIONAL:
                score += FACADE_PENALTY
        if partners:
            score += (
                -NEXT_TO_BONUS if beside else min(s.segment.box.gap(segment.box) for s in partners)
            )
        return score

    def _wanted_beside(self, request: Request, state: SegmentState) -> bool:
        """The segment holds a room the request wants to be `next_to`, or one that wants to
        be next to the request (an OR placed after the ICU that needs windows)."""
        return any(
            r.type in request.spec.next_to or request.type in r.spec.next_to
            for slot in state.slots
            for r in _slot_requests(slot)
        )

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

    def _fallback_entries(self, segment: Segment) -> list[RoomEntry]:
        """Every fill room allowed in the segment, for leftovers the preferred ones don't fit."""
        return [
            e
            for e in self.fills
            if self.rules.spec(e.room).min_side <= segment.depth
            and (segment.facade or self.rules.spec(e.room).windows is not WindowRule.REQUIRED)
            and not self._at_limit(e)
        ]

    def _fill(self, state: SegmentState, all_fills: list[RoomEntry]) -> None:
        segment = state.segment
        fills = self._fill_entries(segment, all_fills)
        if not state.slots and state.free_units == 0:
            # Segment narrower than one module: a single room takes the partial modules.
            width = segment.span.width
            fitting = [
                e
                for e in [*fills, *self._fallback_entries(segment)]
                if self._min_width(self.rules.spec(e.room), 0) <= width
            ]
            filler = RoomEntry(room=self.rules.program.cluster_filler, fill=True)
            state.slots.append(self._fill_slot((fitting or [filler])[0], 0, segment))
            return
        while state.free_units > 0:
            fitting = [e for e in fills if self._fill_min_units(e, segment) <= state.free_units]
            if not fitting and not state.slots:
                # Narrow leftover: another fill room, else the cluster filler (storage).
                fitting = [
                    e
                    for e in self._fallback_entries(segment)
                    if self._fill_min_units(e, segment) <= state.free_units
                ]
                if not fitting:
                    # Plain storerooms, no hallway: neighbours may absorb them later.
                    state.slots += self._filler_slots(state.free_units, segment)
                    return
            pool = fitting or fills
            pool = [e for e in pool if not self._at_limit(e)] or pool
            entry = self.rng.choices(pool, weights=[e.weight for e in pool])[0]
            self.fill_counts[entry.room] = self.fill_counts.get(entry.room, 0) + 1
            spec = self.rules.spec(entry.room)
            stackable = spec.cluster or spec.windows is not WindowRule.REQUIRED
            stackable = stackable and entry.limit is None  # a stack holds several
            if stackable and (cluster := self._fill_cluster(entry, state)) is not None:
                # Small fill rooms (coffins, …) in deep strips: stacked along a hallway.
                state.slots.append(cluster)
                continue
            if spec.cluster and state.slots and self._too_deep(entry, segment):
                # No space for another stack, and a full-depth one would be far too big:
                # the row's rooms take the rest (wider stacks, else a storeroom).
                self._spread(state, state.free_units, fills)
                return
            if (cluster := self._facade_cluster(entry, state)) is not None:
                # Small window rooms in deep facade strips: at the facade, back rooms behind.
                state.slots.append(cluster)
                continue
            if state.slots and self._facade_stack(entry, segment) is not None:
                # No room left for another stack: widen the row rather than a huge room.
                self._spread(state, state.free_units, fills)
                return
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

    def _at_limit(self, entry: RoomEntry) -> bool:
        return entry.limit is not None and self.fill_counts.get(entry.room, 0) >= entry.limit

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
        segment = state.segment
        if not full and (entry := self._leftover_entry(extra, segment)) is not None:
            # Only clusters in the row: a room of its own rather than a wider cluster.
            state.slots.append(self._fill_slot(entry, extra, segment))
            return
        targets = preferred or full or [state.slots[-1]]

        def has_room(slot: Slot, tolerance: float) -> bool:
            if not isinstance(slot, FullSlot) or slot.request.spec.vestibule is not None:
                return True
            area = (slot.units + 1) * segment.unit * segment.depth
            return area <= slot.request.spec.area[1] * tolerance

        given = 0
        # Rooms grow up to their maximum first, then a little past it rather than leaving
        # the rest to a storeroom.
        for tolerance in (SMALL_ROOM_TOLERANCE, ABSORB_TOLERANCE):
            while given < extra and (open_ := [t for t in targets if has_room(t, tolerance)]):
                for target in open_[: extra - given]:
                    target.units += 1
                    given += 1
        rest = extra - given
        if rest == 0:
            return
        if rest * segment.unit <= MAX_SLIVER:
            # A sliver too thin to be a useful room widens rooms that stay within bounds,
            # the other rooms of the row too (a cubicle farm beside full meeting rooms).
            others = [s for s in full if s not in targets and not s.request.spec.cluster]
            for tier in (targets, others):
                while given < extra and (open_ := [t for t in tier if has_room(t, SLIVER_LIMIT)]):
                    for target in open_[: extra - given]:
                        target.units += 1
                        given += 1
            rest = extra - given
            if rest == 0:
                return
        elif (entry := self._leftover_entry(rest, segment)) is not None:
            # Every room is full: the rest becomes another fill room if one fits.
            state.slots.append(self._fill_slot(entry, rest, segment))
            return
        filler = RoomEntry(room=self.rules.program.cluster_filler, fill=True)
        if rest * segment.unit >= self.rules.spec(filler.room).min_side:
            state.slots += self._filler_slots(rest, segment)
            return
        # Too little for a room: a cluster's hallway gets wider, else the rooms after all.
        wider = (
            [s for s in state.slots if isinstance(s, Cluster)]
            or [t for t in targets if has_room(t, SLIVER_LIMIT)]
            or targets
        )
        for i in range(rest):
            wider[i % len(wider)].units += 1

    def _join_unit(self, cells: frozenset[Cell], rooms: list[PlannedRoom]) -> bool:
        """Space behind the core between flats only (no room it could open into) goes to the
        flats: each piece (halved while too big for a closet) joins the flat room beside it
        if it runs along that room's whole wall for at least the room's minimum side, else it
        is a closet entered only through that room."""
        touching = [r for r in rooms if r.cells & _around(cells)]
        if any(r.unit is None and self.rules.spec(r.type).transit for r in touching):
            return False
        flats = [r for r in touching if r.unit is not None and r.host is None]
        closet = next(
            (u.front_fill for u in self.rules.program.units.values() if u.front_fill), None
        )
        if not flats or closet is None:
            return False
        spec = self.rules.spec(closet)
        pieces = [cells]
        plan: list[tuple[frozenset[Cell], PlannedRoom]] = []
        while pieces:
            piece = pieces.pop()
            xs, ys = [c.x for c in piece], [c.y for c in piece]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs) + 1, max(ys) + 1
            if min(x1 - x0, y1 - y0) < spec.min_side:
                return False
            if len(piece) > spec.area[1] * SMALL_ROOM_TOLERANCE:  # one per half
                if x1 - x0 >= y1 - y0:
                    half = frozenset(c for c in piece if c.x < (x0 + x1) // 2)
                else:
                    half = frozenset(c for c in piece if c.y < (y0 + y1) // 2)
                pieces += [half, piece - half]
                continue
            around = _around(piece)
            beside = [r for r in flats if r.cells & around]
            if not beside:
                return False
            plan.append((piece, max(beside, key=lambda r: len(r.cells & around))))
        hosts = {id(r.host) for r in rooms if r.host is not None}
        grown: dict[int, PlannedRoom] = {}
        closets: list[tuple[frozenset[Cell], PlannedRoom]] = []
        for piece, room in plan:
            current = grown.get(id(room), room)
            room_spec = self.rules.spec(room.type)
            if (
                id(room) not in hosts
                and _along(piece, room.cells) >= room_spec.min_side
                and len(current.cells) + len(piece) <= room_spec.area[1] * ABSORB_TOLERANCE
            ):
                grown[id(room)] = replace(current, cells=current.cells | piece)
            else:
                closets.append((piece, room))
        for i, room in enumerate(rooms):
            rooms[i] = grown.get(id(room), room)
        for piece, room in closets:
            host = grown.get(id(room), room)
            rooms.append(PlannedRoom(closet, piece, room.unit, host=host))
        return True

    def rooms_behind(self, segment: Segment, cells: frozenset[Cell]) -> list[PlannedRoom]:
        """Rooms for a rectangle behind the core: rooms dropped for lack of space (a piece
        of their size if it is bigger), else a fill room; too big for any, split in two."""
        xs, ys = [c.x for c in cells], [c.y for c in cells]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs) + 1, max(ys) + 1
        short, long = sorted((x1 - x0, y1 - y0))
        filler = self.rules.program.cluster_filler
        rest_min = self.rules.spec(filler).min_side

        def fits(spec: RoomSpec, a: int, b: int) -> bool:
            a, b = sorted((a, b))
            return a >= max(spec.min_side, self._min_width(spec, b))

        def cut(length: int) -> tuple[frozenset[Cell], frozenset[Cell]]:
            """The first `length` cells along the long side, and the rest."""
            if x1 - x0 >= y1 - y0:
                return frozenset(c for c in cells if c.x < x0 + length), frozenset(
                    c for c in cells if c.x >= x0 + length
                )
            return frozenset(c for c in cells if c.y < y0 + length), frozenset(
                c for c in cells if c.y >= y0 + length
            )

        for request in self.dropped:
            spec = request.spec
            if (
                request.type in self.rules.program.units
                or spec.vestibule is not None
                or (spec.windows is WindowRule.REQUIRED and not segment.facade)
                or (spec.facade_door is not None and not segment.facade)  # needs its door
                or len(cells) < max(request.min_area, spec.area[0])
            ):
                continue
            whole = len(cells) <= spec.area[1] * SMALL_ROOM_TOLERANCE and fits(spec, short, long)
            # Only its own size (else its minimum) while other rooms wait for space, or if
            # the rest is too big for it.
            areas = [request.area, max(request.min_area, spec.area[0])]
            lengths = [max(spec.min_side, math.ceil(area / short)) for area in areas]
            lengths = [n for n in lengths if long - n >= rest_min and fits(spec, short, n)]
            others = [r for r in self.dropped if r is not request]
            if lengths and (not whole or others):
                length = lengths[0]
                if others:  # the longest that leaves the next one its minimum
                    need = max(others[0].min_area, others[0].spec.area[0])
                    length = next((n for n in lengths if (long - n) * short >= need), length)
                piece, rest = cut(length)
            elif whole:
                piece, rest = cells, frozenset[Cell]()
            else:
                continue
            self.dropped.remove(request)
            if not any(r.type == request.type for r in self.dropped):
                warning = f"{self.floor_name}: dropped {request.type} (no space)"
                if warning in self.warnings:
                    self.warnings.remove(warning)
            rooms = [PlannedRoom(request.type, piece)]
            return rooms + (self.rooms_behind(segment, rest) if rest else [])
        if (back := self.core_back) is not None:
            spec = self.rules.spec(back)
            entries = [e for e in self.fills if e.room == back]
            if fits(spec, short, long) and not any(self._at_limit(e) for e in entries):
                self.fill_counts[back] = self.fill_counts.get(back, 0) + 1
                return [PlannedRoom(back, cells, leftover=back == filler)]
        if (kind := self._leftover_room(segment, short, long)) is not None:
            return [PlannedRoom(kind, cells)]
        if long >= 2 * rest_min and len(cells) > self.rules.spec(filler).area[1] * (
            SMALL_ROOM_TOLERANCE
        ):
            first, second = cut(long // 2)
            return self.rooms_behind(segment, first) + self.rooms_behind(segment, second)
        return [PlannedRoom(filler, cells, leftover=True)]

    def _leftover_entry(self, units: int, segment: Segment) -> RoomEntry | None:
        """A fill room that takes `units` of full-depth leftover at its size, if there is one."""
        width = units * segment.unit
        room = self._leftover_room(segment, width, segment.depth, units=True)
        return None if room is None else RoomEntry(room=room, fill=True)

    def _leftover_room(
        self, segment: Segment, width: int, depth: int, units: bool = False, probe: bool = False
    ) -> str | None:
        """A fill room type for a `width` x `depth` leftover: windows first on facades.

        Units (flats) only if `units`: they must go through a slot to be subdivided.
        """
        fitting: list[list[str]] = [[], []]
        for entry in self._fallback_entries(segment):
            spec = self.rules.spec(entry.room)
            high = (entry.area or spec.area)[1]
            if (
                (entry.room in self.rules.program.units and not units)
                or spec.vestibule is not None
                or spec.stalls is not None
                or min(width, depth) < max(spec.min_side, self._min_width(spec, max(width, depth)))
                or width * depth > high * SMALL_ROOM_TOLERANCE
            ):
                continue
            fitting[spec.windows is not WindowRule.REQUIRED].append(entry.room)
        choices = fitting[0] or fitting[1]
        if not choices:
            return None
        room = self.rng.choice(choices)
        if not probe:  # `probe`: only asks whether one fits
            self.fill_counts[room] = self.fill_counts.get(room, 0) + 1  # against its `limit`
        return room

    def _too_deep(self, entry: RoomEntry, segment: Segment) -> bool:
        """A full-depth slot of this fill room would be far above its maximum area."""
        spec = self.rules.spec(entry.room)
        high = (entry.area or spec.area)[1]
        return self._is_small(Request(entry.room, spec, high, Priority.OPTIONAL, None), segment)

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
            [(self._fill_request(entry, depth * column), depth)] * count for _ in range(columns)
        ]
        return Cluster(units, hall, columns, column, stacks)

    def _facade_cluster(self, entry: RoomEntry, state: SegmentState) -> Cluster | None:
        """Fill rooms needing windows in deep facade strips: two columns of facade stacks."""
        if (found := self._facade_stack(entry, state.segment)) is None:
            return None
        column, stack = found
        hall = math.ceil(self.hallway / state.segment.unit) * state.segment.unit
        for columns in (2, 1):
            units = (hall + columns * column) // state.segment.unit
            if units <= state.free_units:
                return Cluster(units, hall, columns, column, [list(stack) for _ in range(columns)])
        return None

    def _facade_stack(
        self, entry: RoomEntry, segment: Segment
    ) -> tuple[int, list[tuple[Request, int]]] | None:
        """Column width and rooms (backs first) of a facade stack, if the strip needs one."""
        spec = self.rules.spec(entry.room)
        low, high = entry.area or spec.area
        column = self._facade_column(spec, low, high, segment)
        if column is None:
            return None
        front = min(high // column, max(spec.min_side, round((low + high) / 2 / column)))
        stack = self._backs(segment, column, front)
        if stack is None:
            return None
        stack.append((Request(entry.room, spec, front * column, Priority.OPTIONAL, None), front))
        return column, stack

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
        back_min = self.rules.spec(self._back_entry(segment.depth - spec.min_side).room).min_side
        smallest = max(MIN_CLUSTER_COLUMN, spec.min_side, back_min)
        column = max(smallest, round(math.sqrt((low + high) / 2) / unit) * unit)
        return math.ceil(column / unit) * unit

    def _back_entry(self, rest: int) -> RoomEntry:
        """The room behind facade stacks: the floor's first windowless fill room that fits
        `rest` cells deep (else the first one at all), or storage."""
        backs = [
            e
            for e in sorted(self.fills, key=lambda e: e.priority is Priority.OPTIONAL)
            if self.rules.spec(e.room).windows is not WindowRule.REQUIRED
            and not self.rules.spec(e.room).circulation
            and e.limit is None  # a limited room (one laundry) can't back every stack
        ]
        fitting = [e for e in backs if self.rules.spec(e.room).min_side <= rest]
        return (fitting or backs or [RoomEntry(room=self.rules.program.cluster_filler, fill=True)])[
            0
        ]

    def _backs(self, segment: Segment, column: int, front: int) -> list[tuple[Request, int]] | None:
        """Back rooms filling the strip from the corridor up to a facade room `front` deep."""
        rest = segment.depth - front
        back = self._back_entry(rest)
        spec = self.rules.spec(back.room)
        if rest < spec.min_side:
            return None
        high = (back.area or spec.area)[1]
        count = max(1, min(rest // spec.min_side, math.ceil(rest * column / high)))
        depths = [rest // count] * count
        depths[-1] += rest - sum(depths)
        return [(self._fill_request(back, d * column), d) for d in depths]

    def _fill_min_units(self, entry: RoomEntry, segment: Segment) -> int:
        spec = self.rules.spec(entry.room)
        width = self._min_width(spec, segment.depth)
        if spec.vestibule is not None:
            width += self.rules.spec(spec.vestibule).min_side
        return math.ceil(width / segment.unit)

    def _filler_slots(self, units: int, segment: Segment) -> list[FullSlot]:
        """`units` of storerooms side by side, as few as keep each near its maximum area."""
        filler = RoomEntry(room=self.rules.program.cluster_filler, fill=True)
        spec = self.rules.spec(filler.room)
        least = max(1, math.ceil(spec.min_side / segment.unit))
        most = int(spec.area[1] * SMALL_ROOM_TOLERANCE / segment.unit / segment.depth)
        return [self._fill_slot(filler, w, segment) for w in _split(units, least, most)]

    def _fill_slot(self, entry: RoomEntry, units: int, segment: Segment) -> FullSlot:
        area = units * segment.unit * segment.depth
        return FullSlot(self._fill_request(entry, area), units)

    def _fill_request(self, entry: RoomEntry, area: int) -> Request:
        spec = self.rules.spec(entry.room)
        leftover = entry.room == self.rules.program.cluster_filler
        return Request(entry.room, spec, area, Priority.OPTIONAL, None, leftover=leftover)

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
                cells = self._rect(segment, span)
                rooms.append(PlannedRoom(slot.request.type, cells, leftover=slot.request.leftover))
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
        """< 0 to sort towards u0, > 0 towards u1, 0 anywhere; rooms opening onto the lobby
        go right beside it, before other rooms `near` the entrance."""
        request = next(iter(_slot_requests(slot)), None)
        if request is None:
            return 0
        if self.anchors.opens_onto_lobby(request.spec):
            anchor, strength = self.anchors.entrance, 2
        else:
            anchor, strength = self.anchors.get(request.near), 1
        if anchor is None:
            return 0
        anchor_u, _ = segment.frame.to_local(*anchor.centre)
        return -strength if anchor_u < segment.span.centre else strength

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
        wide = extra >= filler_min
        if wide and self._leftover_room(segment, extra, band.depth, probe=True) is None:
            # No room fits the extra width: wider columns, as far as their rooms may grow
            # (the last one also takes a rest of the depth too small for a storeroom).
            def deepest(stack: list[tuple[Request, int]]) -> list[tuple[Request, int]]:
                rest = band.depth - sum(d for _, d in stack)
                if not stack or rest >= filler_min:
                    return stack
                return [*stack[:-1], (stack[-1][0], stack[-1][1] + rest)]

            most = min(
                (
                    int(r.spec.area[1] * ABSORB_TOLERANCE) // d
                    for stack in cluster.stacks
                    for r, d in deepest(stack)
                ),
                default=column_width,
            )
            grow = min(extra // cluster.columns, max(0, most - column_width))
            column_width += grow
            extra -= grow * cluster.columns
        if extra >= filler_min:
            u0 = span.u0
            if (room := self._leftover_room(segment, extra, band.depth)) is not None:
                widths, kind = [extra], room
            else:
                most = int(self.rules.spec(filler).area[1] * SMALL_ROOM_TOLERANCE / band.depth)
                widths, kind = _split(extra, filler_min, most), filler
            for width in widths:
                cells = self._rect(segment, Interval(u0, u0 + width))
                rooms.append(PlannedRoom(kind, cells, leftover=kind == filler))
                u0 += width
            span = Interval(u0, span.u1)
        if cluster.columns == 2:
            columns = [
                Interval(span.u0, span.u0 + column_width),
                Interval(span.u1 - column_width, span.u1),
            ]
            hallway = Interval(columns[0].u1, columns[1].u0)
        else:
            hallway = Interval(span.u0, span.u1 - column_width)
            columns = [Interval(hallway.u1, span.u1)]

        # Depth the stacks leave free at the far end: a fill room across the whole cluster,
        # entered from the end of the hallway (like a facade stack's window room).
        used = max(sum(d for _, d in stack) for stack in cluster.stacks)
        back = self._leftover_room(segment, span.width, band.depth - used) if used else None
        depth = used if back is not None else band.depth
        cells = self._depth_rect(segment, hallway, 0, depth)
        rooms.append(PlannedRoom("corridor", cells, hallway=True))
        if back is not None:
            rooms.append(PlannedRoom(back, self._depth_rect(segment, span, depth, band.depth)))

        for column, stack in zip(columns, cluster.stacks, strict=True):
            depths = [d for _, d in stack]
            types = [(r.type, r.leftover) for r, _ in stack]
            rest = depth - sum(depths)
            # The stacked rooms grow into the rest (last first), a storeroom takes what's left.
            for i in reversed(range(len(stack))):
                most = int(stack[i][0].spec.area[1] * ABSORB_TOLERANCE) // column_width
                grow = max(0, min(rest, most - depths[i]))
                depths[i] += grow
                rest -= grow
            if rest >= filler_min or not stack:
                most = int(self.rules.spec(filler).area[1] * SMALL_ROOM_TOLERANCE) // column_width
                for part in _split(rest, filler_min, most):
                    types.append((filler, True))
                    depths.append(part)
            else:
                depths[-1] += rest
            offset = 0
            for (room_type, leftover), room_depth in zip(types, depths, strict=True):
                cells = self._depth_rect(segment, column, offset, offset + room_depth)
                rooms.append(PlannedRoom(room_type, cells, leftover=leftover))
                offset += room_depth
        return rooms

    @staticmethod
    def _depth_rect(segment: Segment, span: Interval, d0: int, d1: int) -> frozenset[Cell]:
        """Cells of `span` from `d0` to `d1` cells away from the strip's corridor side."""
        band = segment.band
        if band.corridor_at is LocalSide.V0:
            return segment.frame.rect(span.u0, span.u1, band.v0 + d0, band.v0 + d1)
        return segment.frame.rect(span.u0, span.u1, band.v1 - d1, band.v1 - d0)


def _around(cells: frozenset[Cell]) -> set[Cell]:
    """The cells next to `cells` (sharing a side with one of them)."""
    return {c.neighbour(side) for c in cells for side in Side} - cells


def _along(piece: frozenset[Cell], room: frozenset[Cell]) -> int:
    """Length of the piece's side that lies wholly against `room`, else 0."""
    xs, ys = [c.x for c in piece], [c.y for c in piece]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs) + 1, max(ys) + 1
    sides = [
        [Cell(x0 - 1, y) for y in range(y0, y1)],
        [Cell(x1, y) for y in range(y0, y1)],
        [Cell(x, y0 - 1) for x in range(x0, x1)],
        [Cell(x, y1) for x in range(x0, x1)],
    ]
    return max((len(side) for side in sides if all(c in room for c in side)), default=0)


def _split(total: int, least: int, most: int) -> list[int]:
    """`total` in as few parts of at most `most` as possible, each at least `least`."""
    count = max(1, min(math.ceil(total / max(most, least)), total // least))
    return [total // count + (i < total % count) for i in range(count)]


def _first_type(slot: Slot) -> str | None:
    if isinstance(slot, FullSlot):
        return slot.request.type
    return next((r.type for stack in slot.stacks for r, _ in stack), None)


def _slot_requests(slot: Slot) -> list[Request]:
    if isinstance(slot, FullSlot):
        return [slot.request]
    return [r for stack in slot.stacks for r, _ in stack]


def _share_side(a: Box, b: Box) -> bool:
    """The boxes touch along a side (not just at a corner)."""
    across = min(a.x1, b.x1) - max(a.x0, b.x0)
    down = min(a.y1, b.y1) - max(a.y0, b.y0)
    return (across > 0 and down == 0) or (down > 0 and across == 0)


def _slot_types(state: SegmentState) -> set[str]:
    return {r.type for s in state.slots for r in _slot_requests(s)}


def _neighbours_together(slots: list[Slot]) -> list[Slot]:
    """Move each slot with a `next_to` room beside a slot holding such a room: a full slot
    right after it, a cluster right before a full slot (its column is at its u1 end, so
    every room stacked in it touches the slot after it)."""
    ordered = list(slots)

    def wants(slot: Slot, other: Slot) -> bool:
        types = {r.type for r in _slot_requests(other)}
        return other is not slot and any(types & set(r.spec.next_to) for r in _slot_requests(slot))

    for slot in slots:
        if isinstance(slot, FullSlot):
            target = next((s for s in ordered if wants(slot, s)), None)
            if target is None:
                continue
            ordered.remove(slot)
            ordered.insert(ordered.index(target) + 1, slot)
        else:
            target = next((s for s in ordered if isinstance(s, FullSlot) and wants(slot, s)), None)
            if target is None:
                continue
            ordered.remove(slot)
            ordered.insert(ordered.index(target), slot)
    return ordered
