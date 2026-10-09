"""Footprints shaped like real buildings: house outlines from OpenStreetMap (ADR 0017).

`data/osm_footprints.yaml` holds outlines (`tools/osm_footprints.py`) as rows of cells. One is
picked by the seed among those whose proportions are closest to the requested width and depth,
mirrored or turned at random, and resampled to exactly width x depth.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from functools import cache
from importlib import resources

import yaml

from roomplanner.errors import InfeasibleError
from roomplanner.geometry import Cell, connected, thinnest_extent
from roomplanner.pipeline.base import Context
from roomplanner.pipeline.footprint import arm_depth
from roomplanner.pipeline.registry import register

TOP = 8  # outlines drawn from the best this many (by proportion)
MISMATCH = 0.6  # score penalty of an outline from another kind of building
# Outline categories (tools/osm_footprints.py) that suit a building type; others get MISMATCH.
KINDS: dict[str, tuple[str, ...]] = {
    "apartment": ("apartments", "house"),
    "safehouse": ("house", "apartments"),
    "mage_flat": ("house", "apartments"),
    "coffin_block": ("apartments", "warehouse"),
    "hotel": ("hotel", "apartments"),
    "office": ("office", "commercial", "government"),
    "corp_office": ("office", "commercial"),
    "corp_lab": ("office", "university", "industrial"),
    "data_centre": ("industrial", "office", "warehouse"),
    "clinic": ("hospital", "commercial", "office"),
    "cosmetic_clinic": ("commercial", "office"),
    "street_doc": ("commercial", "house"),
    "hospital": ("hospital", "university"),
    "police_station": ("government", "office"),
    "prison": ("government", "industrial", "hospital"),
    "church": ("church",),
    "factory": ("industrial", "warehouse"),
    "warehouse": ("warehouse", "industrial"),
    "chop_shop": ("warehouse", "industrial"),
    "parking_garage": ("parking", "industrial"),
    "supermarket": ("commercial", "warehouse"),
    "casino": ("hotel", "commercial"),
    "nightclub": ("commercial", "warehouse"),
}


@dataclass(frozen=True)
class Outline:
    ident: str
    rows: tuple[str, ...]
    category: str = ""

    @property
    def width(self) -> int:
        return len(self.rows[0])

    @property
    def height(self) -> int:
        return len(self.rows)


@cache
def outlines() -> tuple[Outline, ...]:
    text = (resources.files("roomplanner") / "data" / "osm_footprints.yaml").read_text("utf-8")
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    return tuple(
        Outline(e["id"], tuple(e["rows"]), e.get("category", ""))
        for e in yaml.load(text, Loader=loader)
    )


def _resample(
    outline: Outline, width: int, height: int, transpose: bool, flip_x: bool, flip_y: bool
) -> frozenset[Cell]:
    rows = outline.rows
    sw, sh = outline.width, outline.height
    if transpose:
        sw, sh = sh, sw
    cells: set[Cell] = set()
    for y in range(height):
        sy = min(sh - 1, y * sh // height + sh // (2 * height))
        for x in range(width):
            sx = min(sw - 1, x * sw // width + sw // (2 * width))
            a, b = (sy, sx) if transpose else (sx, sy)  # source column, row
            if rows[b][a] == "#":
                cells.add(Cell(width - 1 - x if flip_x else x, height - 1 - y if flip_y else y))
    return frozenset(cells)


def _fits(cells: frozenset[Cell], arm: int) -> bool:
    return len(cells) > 0 and connected(cells) and thinnest_extent(cells, cells, arm) >= arm


@register("footprint", "osm")
class OsmFootprint:
    """The outline of a real building, stretched to the requested size."""

    def footprint(self, ctx: Context) -> frozenset[Cell]:
        rng: random.Random = ctx.rng("footprint")
        arm = arm_depth(ctx)
        ratio = ctx.width / ctx.height
        suited = KINDS.get(str(ctx.rules.program.building))
        options: list[tuple[float, Outline, bool]] = []
        for outline in outlines():
            for transpose in (False, True):
                sw, sh = (
                    (outline.height, outline.width)
                    if transpose
                    else (outline.width, outline.height)
                )
                off = abs(sw / sh / ratio - 1)
                if suited and outline.category not in suited:
                    off += MISMATCH
                options.append((off + rng.uniform(0, 0.15), outline, transpose))
        options.sort(key=lambda o: o[0])
        picks = options[:TOP]
        rng.shuffle(picks)
        for _, outline, transpose in [*picks, *options[TOP:]]:
            flip_x, flip_y = rng.random() < 0.5, rng.random() < 0.5
            cells = _resample(outline, ctx.width, ctx.height, transpose, flip_x, flip_y)
            if _fits(cells, arm):
                return cells
        raise InfeasibleError(
            f"{ctx.width}x{ctx.height} fits no real building outline, every part needs at "
            f"least {arm} cells (try a larger building)"
        )
