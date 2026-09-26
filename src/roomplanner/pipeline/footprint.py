from __future__ import annotations

from roomplanner.geometry import Cell, rectangle
from roomplanner.pipeline.base import Context
from roomplanner.pipeline.registry import register


@register("footprint", "rectangle")
class RectangleFootprint:
    def footprint(self, ctx: Context) -> frozenset[Cell]:
        return rectangle(0, 0, ctx.width, ctx.height)
