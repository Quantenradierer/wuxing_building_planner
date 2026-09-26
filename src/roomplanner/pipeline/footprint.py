"""Footprint strategies: the set of cells a building covers, identical on every floor.

Shapes other than the rectangle are the bounding box minus rectangular cut-outs. Every arm
stays as deep as the building's layout needs for its main part.
"""

from __future__ import annotations

from typing import cast

from roomplanner.errors import InfeasibleError
from roomplanner.geometry import Cell, rectangle
from roomplanner.pipeline.base import Context, LayoutStrategy
from roomplanner.pipeline.registry import register, resolve


@register("footprint", "rectangle")
class RectangleFootprint:
    def footprint(self, ctx: Context) -> frozenset[Cell]:
        return rectangle(0, 0, ctx.width, ctx.height)


def arm_depth(ctx: Context) -> int:
    layout = cast(LayoutStrategy, resolve("layout", ctx.rules.program.layout))
    return layout.main_min_depth(ctx)


@register("footprint", "l")
class LFootprint:
    """The bounding box minus one corner of about 1/3 to 1/2 of each side."""

    def footprint(self, ctx: Context) -> frozenset[Cell]:
        arm = arm_depth(ctx)
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


@register("footprint", "u")
class UFootprint:
    """The bounding box minus a courtyard notch in the middle of one side.

    The notch opens on the longer side if both are possible; the two arms and the base keep
    the layout's minimum depth.
    """

    def footprint(self, ctx: Context) -> frozenset[Cell]:
        arm = arm_depth(ctx)
        rng = ctx.rng("footprint")
        # (notch opens north/south, notch width range, notch depth range)
        options: list[tuple[bool, tuple[int, int], tuple[int, int]]] = []
        for horizontal, across, along in (
            (True, ctx.width, ctx.height),
            (False, ctx.height, ctx.width),
        ):
            widths = (max(MIN_COURT, across // 4), across - 2 * arm)
            depths = (max(MIN_COURT, along // 3), along - arm)
            if widths[0] <= widths[1] and depths[0] <= depths[1]:
                options.append((horizontal, widths, depths))
        if not options:
            raise InfeasibleError(
                f"{ctx.width}x{ctx.height} is too small for a U shape, the arms and the base "
                f"need at least {arm} cells each (try {3 * arm}x{2 * arm} or more)"
            )
        horizontal, widths, depths = rng.choice(options)
        across, along = (ctx.width, ctx.height) if horizontal else (ctx.height, ctx.width)
        notch_w, notch_d = rng.randint(*widths), rng.randint(*depths)
        left = rng.randint(arm, across - arm - notch_w)
        start = 0 if rng.random() < 0.5 else along - notch_d
        if horizontal:
            notch = rectangle(left, start, notch_w, notch_d)
        else:
            notch = rectangle(start, left, notch_d, notch_w)
        return rectangle(0, 0, ctx.width, ctx.height) - notch


MIN_COURT = 4  # cells; narrowest courtyard or notch
