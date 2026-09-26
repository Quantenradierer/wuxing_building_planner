from __future__ import annotations

from roomplanner.errors import InfeasibleError
from roomplanner.geometry import Cell, rectangle
from roomplanner.pipeline.base import Context
from roomplanner.pipeline.registry import register


@register("footprint", "rectangle")
class RectangleFootprint:
    def footprint(self, ctx: Context) -> frozenset[Cell]:
        return rectangle(0, 0, ctx.width, ctx.height)


@register("footprint", "l")
class LFootprint:
    """The bounding box minus one corner of about 1/3 to 1/2 of each side.

    Both arms stay at least one corridor plus one row of rooms wide.
    """

    def footprint(self, ctx: Context) -> frozenset[Cell]:
        program = ctx.rules.program
        arm = program.corridor.width + program.strip_depth[0]
        cuts: list[tuple[int, int]] = []
        for side in (ctx.width, ctx.height):
            low, high = max(side // 3, arm // 2), min(side // 2, side - arm)
            if low > high:
                raise InfeasibleError(
                    f"{ctx.width}x{ctx.height} is too small for an L shape, both arms need "
                    f"at least {arm} cells (try {2 * arm}x{2 * arm} or more)"
                )
            cuts.append((low, high))
        rng = ctx.rng("footprint")
        cut_w, cut_h = rng.randint(*cuts[0]), rng.randint(*cuts[1])
        x = 0 if rng.random() < 0.5 else ctx.width - cut_w
        y = 0 if rng.random() < 0.5 else ctx.height - cut_h
        return rectangle(0, 0, ctx.width, ctx.height) - rectangle(x, y, cut_w, cut_h)
