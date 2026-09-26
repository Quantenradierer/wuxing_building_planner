"""Hall layout: one big room per floor (sales floor) plus back-of-house rooms.

The main part's depth becomes [hall | service corridor | back-of-house strip], the hall on
the street side. Everything else — core, service entrance, L-shaped wings, allocation of
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
        return program.strip_depth[0] + program.corridor.width + _hall_min(ctx)

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
        corridor = program.corridor.width
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
        if hall_first:
            hall = depth - back - corridor
            bands = [
                Band(0, BandKind.HALL, 0, hall),
                Band(1, BandKind.CORRIDOR, hall, hall + corridor),
                Band(2, BandKind.STRIP, hall + corridor, depth, LocalSide.V0, facade=True),
            ]
        else:
            bands = [
                Band(0, BandKind.STRIP, 0, back, LocalSide.V1, facade=True),
                Band(1, BandKind.CORRIDOR, back, back + corridor),
                Band(2, BandKind.HALL, back + corridor, depth),
            ]
        return Part(frame, Grid.centred(program.facade.module, frame.length), bands)


def _hall_min(ctx: Context) -> int:
    if ctx.rules.program.hall is None:
        raise RulesError(f"{ctx.rules.program.building}: the hall layout needs `hall:` settings")
    return ctx.rules.program.hall.min_depth
