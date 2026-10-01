"""Hall layout: one big room per floor (sales floor) plus back-of-house rooms.

The main part's depth becomes [hall | service corridor | back-of-house strip], the hall on
the street side; parts shorter than `hall.corridor_from` have no corridor, their back rooms
open onto the hall. Everything else — core, service entrance, L-shaped wings, allocation of
the back-of-house rooms — works as in the corridor layout.
"""

from __future__ import annotations

import random

from roomplanner.errors import RulesError
from roomplanner.pipeline.base import AllocationError, Context, PlannedRoom
from roomplanner.pipeline.layout.corridor import CorridorLayout, Part
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
from roomplanner.rules import CoreEntry

BACK_OF_HOUSE_SHARE = 0.3  # of the depth, within the program's strip_depth


@register("layout", "hall")
class HallLayout(CorridorLayout):
    def main_min_depth(self, ctx: Context) -> int:
        program = ctx.rules.program
        corridor = program.corridor.width if _has_corridor(ctx) else 0
        return program.strip_depth[0] + corridor + _hall_min(ctx)

    def main_corridor(self, ctx: Context, frame: Frame) -> int:
        return ctx.rules.program.corridor.width if _has_corridor(ctx, frame.length) else 0

    def _part(
        self,
        ctx: Context,
        frame: Frame,
        street: LocalSide,
        rng: random.Random,
        junction: LocalSide | None = None,
        main: bool = False,
    ) -> Part:
        if not main:  # wings keep plain corridors and strips
            return super()._part(ctx, frame, street, rng, junction)
        return self._hall_part(ctx, frame, street, rng, junction)

    def _hall_part(
        self,
        ctx: Context,
        frame: Frame,
        street: LocalSide,
        rng: random.Random,
        junction: LocalSide | None,
    ) -> Part:
        program = ctx.rules.program
        low, high = program.strip_depth
        corridor = program.corridor.width if _has_corridor(ctx, frame.length) else 0
        depth = frame.depth
        back = min(high, max(low, round(depth * BACK_OF_HOUSE_SHARE)))
        back = min(back, depth - corridor - _hall_min(ctx))
        # Hall towards the street; back of house towards a wing (connectors need a strip).
        if junction is not None and not junction.is_end:
            hall_first = junction is LocalSide.V1
        elif not street.is_end:
            hall_first = street is LocalSide.V0
        else:
            hall_first = rng.random() < 0.5
        # Without a corridor the back rooms' "corridor side" is the hall they open onto.
        hall = depth - back - corridor
        if hall_first:
            kinds = [(BandKind.HALL, hall), (BandKind.CORRIDOR, corridor), (BandKind.STRIP, back)]
        else:
            kinds = [(BandKind.STRIP, back), (BandKind.CORRIDOR, corridor), (BandKind.HALL, hall)]
        bands: list[Band] = []
        v = 0
        for kind, size in kinds:
            if size == 0:
                continue
            if kind is BandKind.STRIP:
                side = LocalSide.V0 if hall_first else LocalSide.V1
                bands.append(Band(len(bands), kind, v, v + size, side, facade=True))
            else:
                bands.append(Band(len(bands), kind, v, v + size))
            v += size
        return Part(frame, Grid.centred(program.facade.module, frame.length), bands)

    def _hall_core(
        self, ctx: Context, main: Part, entries: list[CoreEntry], rng: random.Random
    ) -> list[PlannedRoom]:
        """Public stairs in the hall: side by side against its back edge, long side along
        it, open to the hall (see `open`). Hall is left at both ends if it can be, else
        they stand at an end of the hall (the emergency exit there is in the middle of the
        hall's depth, clear of them); they keep clear of connector stubs entering the hall
        from behind."""
        band = next(b for b in main.bands if b.kind is BandKind.HALL)
        sizes = [sorted(e.size) for e in entries]  # (deep, long)
        deep = max(d for d, _ in sizes)
        width = sum(long for _, long in sizes)
        gap = _hall_room_min(ctx)
        behind = main.bands[band.index + 1 if band.index == 0 else band.index - 1]
        stubs = main.reserved.get(behind.index, [])
        inside: list[int] = []  # hall at both ends
        flush: list[int] = []  # at an end of the hall (or of a piece of it)
        for span in (
            free_intervals(main.frame.length, main.blocked(band))
            if band.depth - deep >= gap
            else []
        ):
            for u0 in range(span.u0, span.u1 - width + 1):
                stairs = Interval(u0, u0 + width)
                if any(stairs.overlaps(s) for s in stubs):
                    continue
                left, right = u0 - span.u0, span.u1 - stairs.u1
                if left >= gap and right >= gap:
                    inside.append(u0)
                elif (left >= gap or left == 0) and (right >= gap or right == 0):
                    flush.append(u0)
        if not inside and not flush:
            raise AllocationError("no space for the stairs in the hall")
        u0 = rng.choice(inside or flush)
        back_at_v1 = band.index == 0  # the hall's back edge borders the corridor or strip
        rooms: list[PlannedRoom] = []
        for entry, (d, long) in zip(entries, sizes, strict=True):
            v0, v1 = (band.v1 - d, band.v1) if back_at_v1 else (band.v0, band.v0 + d)
            rooms.append(PlannedRoom(entry.room, main.frame.rect(u0, u0 + long, v0, v1)))
            u0 += long
        return rooms


def _has_corridor(ctx: Context, length: int | None = None) -> bool:
    """A service corridor at this main part length (unknown: the building's longer side)."""
    if length is None:
        length = max(ctx.params.width, ctx.params.depth)
    hall = ctx.rules.program.hall
    return hall is None or length >= hall.corridor_from


def _hall_min(ctx: Context) -> int:
    """Minimum hall depth: deeper with stairs in it, so the hall stays wide enough beside them."""
    if ctx.rules.program.hall is None:
        raise RulesError(f"{ctx.rules.program.building}: the hall layout needs `hall:` settings")
    in_hall = [min(c.size) for c in ctx.rules.active_core(ctx.params) if c.place == "hall"]
    stairs = max(in_hall) + _hall_room_min(ctx) if in_hall else 0
    return max(ctx.rules.program.hall.min_depth, stairs)


def _hall_room_min(ctx: Context) -> int:
    """The largest `min_side` of the hall rooms (sales floor, stockroom) of all floor roles."""
    roles = ctx.rules.program.floor_roles.values()
    halls = {e.room for role in roles for e in role.rooms if e.place == "hall"}
    return max((ctx.rules.spec(room).min_side for room in halls), default=0)
