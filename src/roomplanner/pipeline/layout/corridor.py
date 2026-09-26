"""Corridor-first layout for rectangular footprints. See docs/architecture.md, "Pipeline".

1. Split the depth into bands: rows of rooms ("strips") and corridors running along the
   long axis. Shallow buildings get one single-loaded corridor, deeper ones one central
   corridor, very deep ones several parallel corridors joined by a cross corridor.
2. Reserve building-wide slots: cross corridor and vertical core (identical on every floor).
3. Per floor: reserve the ground floor's lobby and service corridor stub, then let the
   allocator fill the remaining strip segments with the floor role's rooms.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from roomplanner.geometry import CELL_AREA_M2, CELL_SIZE_M, Cell
from roomplanner.model import level_name
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
    Frame,
    Grid,
    Interval,
    LocalSide,
    free_intervals,
)
from roomplanner.pipeline.registry import register
from roomplanner.rules import CoreEntry, EntranceKind, Priority, RoomEntry, evaluate, variables

MIN_GAP_MODULES = 1  # free space left next to reserved slots, if any


@dataclass(frozen=True)
class Skeleton:
    """Everything shared by all floors."""

    frame: Frame
    grid: Grid
    bands: list[Band]
    cross: Interval | None
    core_band: Band | None
    core_slot: Interval | None
    core_rooms: list[PlannedRoom]
    lobby_slice: Interval | None  # ground-floor lobby spanning all bands at a short end


@register("layout", "corridor")
class CorridorLayout:
    def check_feasibility(self, ctx: Context, footprint: frozenset[Cell]) -> list[str]:
        program = ctx.rules.program
        frame = Frame.for_size(ctx.width, ctx.height)
        corridor = ctx.cells(program.corridor.width_m)
        min_depth = ctx.cells(program.strip_depth_m[0])
        problems: list[str] = []
        if frame.depth < corridor + min_depth:
            problems.append(
                f"building is {frame.depth * CELL_SIZE_M:g} m across, needs at least "
                f"{(corridor + min_depth) * CELL_SIZE_M:g} m for a corridor and a row of rooms"
            )
        core = sum(
            ctx.area_cells(c.size_m[0] * c.size_m[1]) for c in ctx.rules.active_core(ctx.params)
        )
        for level in ctx.params.levels:
            _, role = ctx.rules.role_for(level, ctx.params)
            needed = core + corridor * frame.length
            for entry in role.rooms:
                if entry.priority is Priority.REQUIRED and evaluate(
                    entry.when, variables(ctx.params, level)
                ):
                    low = (entry.area_m2 or ctx.rules.spec(entry.room).area_m2)[0]
                    needed += ctx.area_cells(low) * entry.count_range[0]
            if needed > len(footprint):
                problems.append(
                    f"{level_name(level)}: core, corridor and required rooms need "
                    f"{needed * CELL_AREA_M2:g} m², "
                    f"the floor has {len(footprint) * CELL_AREA_M2:g} m²"
                )
        return problems

    def layout(self, ctx: Context, footprint: frozenset[Cell]) -> BuildingPlan:
        rng = ctx.rng("layout")
        skeleton = self._skeleton(ctx, rng)
        plan = BuildingPlan(floors=[])
        for level in ctx.params.levels:
            plan.floors.append(self._floor(ctx, skeleton, level, rng, plan.warnings))
        return plan

    # --- skeleton ---------------------------------------------------------------------

    def _skeleton(self, ctx: Context, rng: random.Random) -> Skeleton:
        program = ctx.rules.program
        frame = Frame.for_size(ctx.width, ctx.height)
        grid = Grid.centred(ctx.cells(program.facade.module_m), frame.length)
        corridor_width = ctx.cells(program.corridor.width_m)
        street = frame.local(ctx.params.street_side)
        bands = self._bands(ctx, frame, corridor_width, street, rng)

        lobby_slice = None
        lobby = self._lobby_entry(ctx)
        if lobby is not None and street.is_end:
            length = max(
                math.ceil(self._area(ctx, lobby, rng) / frame.depth),
                ctx.cells(ctx.rules.spec(lobby.room).min_side_m),
            )
            if street is LocalSide.U0:
                lobby_slice = Interval(0, grid.ceil(length))
            else:
                lobby_slice = Interval(grid.floor(frame.length - length), frame.length)

        blocked = [lobby_slice] if lobby_slice else []
        cross = None
        if sum(b.kind is BandKind.CORRIDOR for b in bands) > 1:
            target = frame.length / 2 + rng.uniform(-1, 1) * frame.length / 8
            cross = self._choose(grid, grid.round_up(corridor_width), target, blocked)
            if cross is None:
                raise AllocationError("no space for the cross corridor")
            blocked.append(cross)

        core_band, core_slot, core_rooms = None, None, []
        core_entries = ctx.rules.active_core(ctx.params)
        if core_entries:
            core_band = self._core_band(bands, street, cross, rng)
            core_slot, core_rooms = self._core(
                ctx, frame, grid, core_band, core_entries, cross, blocked, rng
            )

        return Skeleton(frame, grid, bands, cross, core_band, core_slot, core_rooms, lobby_slice)

    def _bands(
        self, ctx: Context, frame: Frame, corridor: int, street: LocalSide, rng: random.Random
    ) -> list[Band]:
        low, high = (ctx.cells(d) for d in ctx.rules.program.strip_depth_m)
        depth = frame.depth
        if depth < corridor + 2 * low:
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

        count = 1
        for k in range(1, depth):
            strip = (depth - k * corridor) / (2 * k)
            if strip < low:
                break
            count = k
            if strip <= high:
                break

        strip_total = depth - count * corridor
        depths = [strip_total // (2 * count)] * (2 * count)
        for i in range(strip_total - sum(depths)):
            depths[i] += 1
        if count == 1:
            lo, hi = max(low, strip_total - high), min(high, strip_total - low)
            if lo <= hi:
                spread = (hi - lo) // 4
                first = min(hi, max(lo, (lo + hi) // 2 + rng.randint(-spread, spread)))
                depths = [first, strip_total - first]

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

    def _core_band(
        self, bands: list[Band], street: LocalSide, cross: Interval | None, rng: random.Random
    ) -> Band:
        strips = [b for b in bands if b.kind is BandKind.STRIP]
        if cross is not None:
            return rng.choice([b for b in strips if not b.facade])
        if len(strips) == 2 and not street.is_end:
            street_strip = strips[0] if street is LocalSide.V0 else strips[1]
            return next(b for b in strips if b is not street_strip)
        return rng.choice(strips)

    def _core(
        self,
        ctx: Context,
        frame: Frame,
        grid: Grid,
        band: Band,
        entries: list[CoreEntry],
        cross: Interval | None,
        blocked: list[Interval],
        rng: random.Random,
    ) -> tuple[Interval, list[PlannedRoom]]:
        """The core occupies one full-depth slot; the first entry (stairwell) wraps the rest."""
        sizes: list[tuple[int, int]] = []  # (u, v) per entry
        for entry in entries:
            short, long = sorted(ctx.cells(s) for s in entry.size_m)
            if short > band.depth:
                raise AllocationError(f"{entry.room} does not fit a {band.depth}-cell strip")
            sizes.append((short, long) if long <= band.depth else (long, short))
        width = sum(u for u, _ in sizes)
        if band.facade:
            width = grid.round_up(width)

        if cross is not None:
            left = cross.u0 - width / 2
            right = cross.u1 + width / 2
            target = left if rng.random() < 0.5 else right
            slot = self._choose(grid, width, target, blocked, touching=cross)
        else:
            target = frame.length / 2 + rng.uniform(-1, 1) * frame.length / 6
            slot = self._choose(grid, width, target, blocked)
        if slot is None:
            raise AllocationError("no space for the core")

        main_min_side = ctx.cells(ctx.rules.spec(entries[0].room).min_side_m)
        rooms: list[PlannedRoom] = []
        taken: set[Cell] = set()
        right_edge = slot.u1
        for entry, (u_size, v_size) in zip(entries[1:], sizes[1:], strict=True):
            if band.depth - v_size < main_min_side:
                v_size = band.depth
            v0, v1 = self._from_corridor(band, v_size)
            cells = frame.rect(right_edge - u_size, right_edge, v0, v1)
            rooms.append(PlannedRoom(entry.room, cells))
            taken |= cells
            right_edge -= u_size
        slot_cells = frame.rect(slot.u0, slot.u1, band.v0, band.v1)
        rooms.insert(0, PlannedRoom(entries[0].room, slot_cells - taken))
        return slot, rooms

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
    ) -> Interval | None:
        """Grid-aligned interval closest to `target` that leaves usable gaps around it."""
        min_gap = MIN_GAP_MODULES * grid.module
        starts = {*grid.points(), *(b.u1 for b in blocked), *(b.u0 - width for b in blocked)}
        options = {Interval(start, start + width) for start in starts}
        # Flush with a building end, widened to the next grid point (partial end modules).
        options |= {
            Interval(0, grid.ceil(width)),
            Interval(grid.floor(grid.length - width), grid.length),
        }
        candidates: list[Interval] = []
        for interval in options:
            start = interval.u0
            if start < 0 or interval.u1 > grid.length:
                continue
            if any(interval.overlaps(b) for b in blocked):
                continue
            if touching is not None and interval.gap_to(touching) != 0:
                continue
            neighbours = [0, grid.length, *(b.u0 for b in blocked), *(b.u1 for b in blocked)]
            left = min((start - n for n in neighbours if n <= start), default=0)
            right = min((n - interval.u1 for n in neighbours if n >= interval.u1), default=0)
            if 0 < left < min_gap or 0 < right < min_gap:
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
        frame, grid = skeleton.frame, skeleton.grid
        role_name, role = ctx.rules.role_for(level, ctx.params)
        rooms: list[PlannedRoom] = list(skeleton.core_rooms)
        reserved: dict[int, list[Interval]] = {b.index: [] for b in skeleton.bands}
        entrance_hints: list[tuple[EntranceKind, LocalSide, Cell]] = []

        if skeleton.core_band is not None and skeleton.core_slot is not None:
            reserved[skeleton.core_band.index].append(skeleton.core_slot)
        if skeleton.cross is not None:
            for band in skeleton.bands:
                if band.kind is BandKind.STRIP:
                    reserved[band.index].append(skeleton.cross)
                    cells = frame.rect(skeleton.cross.u0, skeleton.cross.u1, band.v0, band.v1)
                    rooms.append(PlannedRoom("corridor", cells))

        lobby_anchor: tuple[int | None, Interval] | None = None
        if level == 0:
            lobby_anchor = self._ground_floor(
                ctx, skeleton, rooms, reserved, entrance_hints, rng, warnings
            )

        slice_ = skeleton.lobby_slice if level == 0 else None
        for band in skeleton.bands:
            if band.kind is BandKind.CORRIDOR:
                blocked = [slice_] if slice_ else []
                for span in free_intervals(frame.length, blocked):
                    rooms.append(
                        PlannedRoom("corridor", frame.rect(span.u0, span.u1, band.v0, band.v1))
                    )

        segments: list[Segment] = []
        for band in skeleton.bands:
            if band.kind is not BandKind.STRIP:
                continue
            blocked = reserved[band.index] + ([slice_] if slice_ else [])
            unit = grid.module if band.facade else 1
            for span in free_intervals(frame.length, blocked):
                segments.append(Segment(band, span, unit, grid))

        core_anchor = None
        if skeleton.core_band is not None and skeleton.core_slot is not None:
            core_anchor = (skeleton.core_band.index, skeleton.core_slot)
        allocator = Allocator(
            ctx,
            ctx.rules,
            frame,
            segments,
            Anchors(core_anchor, lobby_anchor),
            ctx.rng(f"allocate:{level}"),
        )
        rooms += allocator.allocate(role, level, level_name(level))
        warnings += allocator.warnings

        rooms = _merge_corridors(rooms)
        entrances = [
            EntranceRequest(kind, frame.side(side), _room_index(rooms, hint), hint)
            for kind, side, hint in entrance_hints
        ]
        return FloorPlan(level, role_name, rooms, entrances)

    def _ground_floor(
        self,
        ctx: Context,
        skeleton: Skeleton,
        rooms: list[PlannedRoom],
        reserved: dict[int, list[Interval]],
        hints: list[tuple[EntranceKind, LocalSide, Cell]],
        rng: random.Random,
        warnings: list[str],
    ) -> tuple[int | None, Interval] | None:
        """Reserve lobby and service stub; returns the lobby as anchor for `near: entrance`."""
        frame, grid = skeleton.frame, skeleton.grid
        program = ctx.rules.program
        street = frame.local(ctx.params.street_side)
        service = frame.local(ctx.params.service_side)
        lobby = self._lobby_entry(ctx)
        anchor: tuple[int | None, Interval] | None = None

        if lobby is not None and skeleton.lobby_slice is not None:
            span = skeleton.lobby_slice
            rooms.append(PlannedRoom(lobby.room, frame.rect(span.u0, span.u1, 0, frame.depth)))
            end_u = 0 if street is LocalSide.U0 else frame.length - 1
            hints.append((EntranceKind.MAIN, street, frame.cell(end_u, frame.depth // 2)))
            anchor = (None, span)
        elif lobby is not None:
            band = self._facade_band(skeleton.bands, street)
            width = max(
                math.ceil(self._area(ctx, lobby, rng) / band.depth),
                ctx.cells(ctx.rules.spec(lobby.room).min_side_m),
            )
            target = frame.length / 2 + rng.uniform(-1, 1) * frame.length / 6
            span = self._choose(grid, grid.round_up(width), target, reserved[band.index])
            if span is None:
                raise AllocationError("no space for the lobby")
            reserved[band.index].append(span)
            rooms.append(PlannedRoom(lobby.room, frame.rect(span.u0, span.u1, band.v0, band.v1)))
            v = 0 if street is LocalSide.V0 else frame.depth - 1
            hints.append((EntranceKind.MAIN, street, frame.cell(int(span.centre), v)))
            anchor = (band.index, span)

        if EntranceKind.SERVICE not in program.entrances:
            return anchor
        corridors = [b for b in skeleton.bands if b.kind is BandKind.CORRIDOR]
        if service is street and anchor is not None:
            hint = hints[0][2]
        elif service.is_end:
            band = rng.choice(corridors)
            u = 0 if service is LocalSide.U0 else frame.length - 1
            hint = frame.cell(u, (band.v0 + band.v1) // 2)
        else:
            band = self._facade_band(skeleton.bands, service)
            v = 0 if service is LocalSide.V0 else frame.depth - 1
            if band.kind is BandKind.CORRIDOR:
                hint = frame.cell(frame.length // 2, v)
            else:
                width = grid.round_up(ctx.cells(program.corridor.width_m))
                target = skeleton.core_slot.centre if skeleton.core_slot else frame.length / 2
                span = self._choose(grid, width, target, reserved[band.index])
                if span is not None:
                    reserved[band.index].append(span)
                    cells = frame.rect(span.u0, span.u1, band.v0, band.v1)
                    rooms.append(PlannedRoom("corridor", cells))
                    hint = frame.cell(int(span.centre), v)
                elif skeleton.core_band is band and skeleton.core_slot is not None:
                    # No space for a corridor stub: the stairwell gets the exit instead.
                    hint = frame.cell(skeleton.core_slot.u0, v)
                else:
                    warnings.append("no space for the service entrance")
                    return anchor
        hints.append((EntranceKind.SERVICE, service, hint))
        return anchor

    # --- helpers ----------------------------------------------------------------------

    @staticmethod
    def _lobby_entry(ctx: Context) -> RoomEntry | None:
        _, role = ctx.rules.role_for(0, ctx.params)
        return next((e for e in role.rooms if e.place == "entrance"), None)

    @staticmethod
    def _area(ctx: Context, entry: RoomEntry, rng: random.Random) -> int:
        low, high = (
            ctx.area_cells(a) for a in (entry.area_m2 or ctx.rules.spec(entry.room).area_m2)
        )
        return rng.randint(low, high)

    @staticmethod
    def _facade_band(bands: list[Band], side: LocalSide) -> Band:
        return bands[0] if side is LocalSide.V0 else bands[-1]


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
