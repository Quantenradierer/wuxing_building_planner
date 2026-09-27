"""Hall layout: one big room per floor (sales floor) plus back-of-house rooms.

The main part's depth becomes [hall | service corridor | back-of-house strip], the hall on
the street side; parts shorter than `hall.corridor_from` have no corridor, their back rooms
open onto the hall. Everything else — core, service entrance, L-shaped wings, allocation of
the back-of-house rooms — works as in the corridor layout.
"""

from __future__ import annotations

import random

from roomplanner.errors import RulesError
from roomplanner.pipeline.base import Context
from roomplanner.pipeline.layout.corridor import CorridorLayout, Part
from roomplanner.pipeline.layout.frame import Band, BandKind, Frame, Grid, LocalSide
from roomplanner.pipeline.registry import register

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


def _has_corridor(ctx: Context, length: int | None = None) -> bool:
    """A service corridor at this main part length (unknown: the building's longer side)."""
    if length is None:
        length = max(ctx.params.width, ctx.params.depth)
    hall = ctx.rules.program.hall
    return hall is None or length >= hall.corridor_from


def _hall_min(ctx: Context) -> int:
    if ctx.rules.program.hall is None:
        raise RulesError(f"{ctx.rules.program.building}: the hall layout needs `hall:` settings")
    return ctx.rules.program.hall.min_depth
