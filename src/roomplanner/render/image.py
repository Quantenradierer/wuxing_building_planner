"""Image renderer: one raster image per floor, drawn from the model (i.e. the JSON) only.

Layers, bottom to top: background, floors with patterns and grain, soft shadows of walls
and objects, objects, walls with windows and doors, room labels, grid. Neon marks (accent
strips, window glass, screens, lights) go to a glow layer that is blurred and added on top.
The picture is drawn at a higher resolution and scaled down for anti-aliasing.
"""

# Pillow's stubs mention numpy types, which are unknown without numpy installed.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from roomplanner.geometry import Axis, Cell, Edge, Side
from roomplanner.model import Building, Floor, Opening, OpeningKind, Room
from roomplanner.render.shapes import SHAPES, Pen
from roomplanner.render.theme import Colour, Pattern, Theme, colour

DEFAULT_CELL_PX = 50
MAX_SUPERSAMPLED_PIXELS = 48_000_000


@dataclass(frozen=True)
class RenderOptions:
    cell_px: int = DEFAULT_CELL_PX
    padding: int = 2  # cells around the building; whole VTT grid squares keep alignment
    labels: bool = False
    grid: int = 0  # grid line every n cells, 0 = none


def render_floor(
    building: Building, floor: Floor, theme: Theme, options: RenderOptions | None = None
) -> Image.Image:
    options = options or RenderOptions()
    columns = building.width + 2 * options.padding
    rows = building.height + 2 * options.padding
    supersample = 2 if columns * rows * (options.cell_px * 2) ** 2 <= MAX_SUPERSAMPLED_PIXELS else 1
    canvas = _Canvas(building, floor, theme, options, options.cell_px * supersample)
    image = canvas.draw()
    if supersample > 1:
        image = image.resize(
            (columns * options.cell_px, rows * options.cell_px), Image.Resampling.LANCZOS
        )
    return image


def render_building(
    building: Building, theme: Theme, options: RenderOptions | None = None
) -> dict[int, Image.Image]:
    """One image per floor level."""
    return {f.level: render_floor(building, f, theme, options) for f in building.floors}


class _Canvas:
    def __init__(
        self, building: Building, floor: Floor, theme: Theme, options: RenderOptions, cell: int
    ) -> None:
        self.building = building
        self.floor = floor
        self.theme = theme
        self.options = options
        self.cell = cell
        self.size = (
            (building.width + 2 * options.padding) * cell,
            (building.height + 2 * options.padding) * cell,
        )
        self.image = Image.new("RGB", self.size, colour(theme.background)[:3])
        self.glow = Image.new("RGB", self.size, (0, 0, 0))
        self.shadow = Image.new("L", self.size, 0)
        self.draw_base = ImageDraw.Draw(self.image, "RGBA")
        self.draw_glow = ImageDraw.Draw(self.glow)
        self.draw_shadow = ImageDraw.Draw(self.shadow)
        self.owner = {cell: room for room in floor.rooms for cell in room.cells}

    # --- coordinates ------------------------------------------------------------------

    def px(self, x: float, y: float) -> tuple[float, float]:
        pad = self.options.padding
        return (x + pad) * self.cell, (y + pad) * self.cell

    def cell_box(self, cell: Cell) -> tuple[float, float, float, float]:
        x0, y0 = self.px(cell.x, cell.y)
        return x0, y0, x0 + self.cell, y0 + self.cell

    def edge_line(self, edge: Edge) -> tuple[float, float, float, float]:
        x0, y0 = self.px(edge.x, edge.y)
        if edge.axis is Axis.H:
            return x0, y0, x0 + self.cell, y0
        return x0, y0, x0, y0 + self.cell

    # --- layers -----------------------------------------------------------------------

    def draw(self) -> Image.Image:
        self._outside()
        for room in self.floor.rooms:
            self._room_floor(room)
        self._grain()
        self._accents()
        self._object_shadows()
        self._walls_shadow()
        self._apply_shadow()
        self._objects()
        self._walls()
        self._openings()
        if self.options.labels:
            self._labels()
        if self.options.grid:
            self._grid()
        return self._compose()

    def _outside(self) -> None:
        if self.theme.outside_pattern is Pattern.PLAIN:
            return
        lines = colour(self.theme.outside_lines)
        self._pattern_lines(
            (0, 0, *self.size), self.theme.outside_pattern, 2, lines, self.draw_base
        )

    def _room_floor(self, room: Room) -> None:
        material = self.theme.material(room.type)
        fill = colour(material.floor)
        for cell in room.cells:
            self.draw_base.rectangle(self.cell_box(cell), fill=fill)
        if material.pattern is Pattern.PLAIN:
            return
        lines = colour(material.lines)
        width = max(1, round(self.cell * 0.03))
        n = material.tile
        for cell in room.cells:
            x0, y0, x1, y1 = self.cell_box(cell)
            match material.pattern:
                case Pattern.TILES:
                    if cell.x % n == 0:
                        self.draw_base.line((x0, y0, x0, y1), fill=lines, width=width)
                    if cell.y % n == 0:
                        self.draw_base.line((x0, y0, x1, y0), fill=lines, width=width)
                case Pattern.PLANKS:
                    if cell.y % n == 0:
                        self.draw_base.line((x0, y0, x1, y0), fill=lines, width=width)
                    if (cell.x + 3 * (cell.y // n)) % (4 * n) == 0:
                        self.draw_base.line((x0, y0, x0, y1), fill=lines, width=width)
                case _:  # grate
                    self._pattern_lines((x0, y0, x1, y1), Pattern.GRATE, 1, lines, self.draw_base)

    def _pattern_lines(
        self,
        box: tuple[float, float, float, float],
        pattern: Pattern,
        spacing_cells: float,
        fill: Colour,
        draw: ImageDraw.ImageDraw,
    ) -> None:
        x0, y0, x1, y1 = box
        width = max(1, round(self.cell * 0.03))
        step = self.cell * spacing_cells / 3 if pattern is Pattern.GRATE else self.cell * 2
        if pattern is Pattern.GRATE:
            offset = 0.0
            while offset < (x1 - x0) + (y1 - y0):
                ax, ay = x0 + offset, y0
                bx, by = x0, y0 + offset
                # Clip the diagonal to the box.
                if ax > x1:
                    ay += ax - x1
                    ax = x1
                if by > y1:
                    bx += by - y1
                    by = y1
                draw.line((ax, ay, bx, by), fill=fill, width=width)
                offset += step
            return
        x = x0
        while x <= x1:
            draw.line((x, y0, x, y1), fill=fill, width=width)
            x += step
        y = y0
        while y <= y1:
            draw.line((x0, y, x1, y), fill=fill, width=width)
            y += step

    def _grain(self) -> None:
        strength = self.theme.noise
        if strength <= 0:
            return
        rng = random.Random(f"{self.building.seed}:{self.floor.level}:grain")
        w, h = max(1, self.size[0] // 6), max(1, self.size[1] // 6)
        noise = Image.frombytes("L", (w, h), rng.randbytes(w * h))
        noise = noise.resize(self.size, Image.Resampling.BICUBIC)
        grey = Image.merge("RGB", (noise, noise, noise))
        footprint = Image.new("L", self.size, 0)
        mask_draw = ImageDraw.Draw(footprint)
        for cell in self.floor.footprint:
            mask_draw.rectangle(self.cell_box(cell), fill=round(255 * strength))
        self.image = Image.composite(ImageChops.overlay(self.image, grey), self.image, footprint)
        self.draw_base = ImageDraw.Draw(self.image, "RGBA")

    def _accents(self) -> None:
        """Neon strips along the walls of rooms whose material has an accent."""
        offset = self.cell * (self.theme.walls.interior / 2 + 0.12)
        width = max(1, round(self.cell * 0.05))
        for edge in self.floor.walls:
            for cell, sign in zip(edge.cells(), (-1, 1), strict=True):
                room = self.owner.get(cell)
                if room is None:
                    continue
                accent = self.theme.material(room.type).accent
                if accent is None:
                    continue
                x0, y0, x1, y1 = self.edge_line(edge)
                # The cell lies at -1 (north / west) or +1 (south / east) of the edge.
                dx, dy = (0.0, sign * offset) if edge.axis is Axis.H else (sign * offset, 0.0)
                line = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
                fill = colour(accent)
                self.draw_glow.line(line, fill=fill[:3], width=width * 2)
                self.draw_base.line(line, fill=(*fill[:3], 160), width=width)

    def _object_shadows(self) -> None:
        shift = self.cell * 0.12
        for obj in self.floor.objects:
            if not obj.blocking:
                continue
            x0, y0 = self.px(obj.x, obj.y)
            x1, y1 = self.px(obj.x + obj.w, obj.y + obj.h)
            self.draw_shadow.rectangle((x0 + shift, y0 + shift, x1 + shift, y1 + shift), fill=200)

    def _walls_shadow(self) -> None:
        shift = self.cell * 0.15
        for edge in self.floor.walls:
            x0, y0, x1, y1 = self._wall_rect(edge)
            self.draw_shadow.rectangle((x0 + shift, y0 + shift, x1 + shift, y1 + shift), fill=255)

    def _apply_shadow(self) -> None:
        small = self.shadow.resize(
            (max(1, self.size[0] // 4), max(1, self.size[1] // 4)), Image.Resampling.BILINEAR
        )
        small = small.filter(ImageFilter.GaussianBlur(self.cell * 0.2 / 4))
        mask = small.resize(self.size, Image.Resampling.BILINEAR)
        shade = colour(self.theme.shadow)
        mask = mask.point([v * shade[3] // 255 for v in range(256)])
        dark = Image.new("RGB", self.size, shade[:3])
        self.image = Image.composite(dark, self.image, mask)
        self.draw_base = ImageDraw.Draw(self.image, "RGBA")

    def _objects(self) -> None:
        for obj in sorted(self.floor.objects, key=lambda o: o.blocking):  # walkable first
            style = self.theme.object_style(obj.kind)
            shape = SHAPES.get(style.shape, SHAPES["box"])
            x0, y0 = self.px(obj.x, obj.y)
            x1, y1 = self.px(obj.x + obj.w, obj.y + obj.h)
            shape(
                Pen(self.draw_base, self.draw_glow, self.cell, style), (x0, y0, x1, y1), obj.facing
            )

    def _is_exterior(self, edge: Edge) -> bool:
        return self.floor.is_exterior_wall(edge)

    def _thickness(self, edge: Edge) -> float:
        walls = self.theme.walls
        return self.cell * (walls.exterior if self._is_exterior(edge) else walls.interior)

    def _wall_rect(self, edge: Edge) -> tuple[float, float, float, float]:
        """The wall body of one edge, extended at both ends so runs and corners join."""
        half = self._thickness(edge) / 2
        x0, y0, x1, y1 = self.edge_line(edge)
        return x0 - half, y0 - half, x1 + half, y1 + half

    def _walls(self) -> None:
        opened = {e for o in self.floor.openings for e in o.edges}
        body = colour(self.theme.walls.colour)
        outline = colour(self.theme.walls.edge)
        rects = [self._wall_rect(e) for e in self.floor.walls if e not in opened]
        width = max(1, round(self.cell * 0.04))
        for rect in rects:  # outline first, the bodies then cover the inner joints
            x0, y0, x1, y1 = rect
            self.draw_base.rectangle((x0 - width, y0 - width, x1 + width, y1 + width), fill=outline)
        for rect in rects:
            self.draw_base.rectangle(rect, fill=body)

    def _openings(self) -> None:
        for opening in self.floor.openings:
            if opening.kind is OpeningKind.WINDOW:
                self._window(opening)
            else:
                self._door(opening)

    def _run(self, opening: Opening) -> tuple[tuple[float, float], tuple[float, float]]:
        first, last = opening.edges[0], opening.edges[-1]
        start = self.px(first.x, first.y)
        _, _, x2, y2 = self.edge_line(last)
        return start, (x2, y2)

    def _window(self, opening: Opening) -> None:
        style = self.theme.windows
        (ax, ay), (bx, by) = self._run(opening)
        half = self._thickness(opening.edges[0]) / 2
        horizontal = opening.axis is Axis.H
        if horizontal:
            frame = (ax, ay - half, bx, by + half)
            glass = (ax, ay - half * 0.35, bx, by + half * 0.35)
        else:
            frame = (ax - half, ay, bx + half, by)
            glass = (ax - half * 0.35, ay, bx + half * 0.35, by)
        self.draw_base.rectangle(frame, fill=colour(style.frame))
        self.draw_base.rectangle(glass, fill=colour(style.glass))
        self.draw_glow.rectangle(glass, fill=colour(style.glow)[:3])

    def _door(self, opening: Opening) -> None:
        style = self.theme.doors
        swing = opening.swing
        if swing is None:
            return
        exterior = self._is_exterior(opening.edges[0])
        leaf_colour = colour(
            style.exterior_leaf if exterior and style.exterior_leaf else style.leaf
        )
        (ax, ay), (bx, by) = self._run(opening)
        length = math.hypot(bx - ax, by - ay)
        tx, ty = swing.towards.delta
        width = max(2, round(self.cell * 0.09))
        # Wide doors are double doors hinged at both ends.
        if len(opening.edges) >= 4:
            leaves = [((ax, ay), (bx, by), length / 2), ((bx, by), (ax, ay), length / 2)]
        else:
            first = swing.hinge in (Side.W, Side.N)
            hinge, other = ((ax, ay), (bx, by)) if first else ((bx, by), (ax, ay))
            leaves = [(hinge, other, length)]
        for (hx, hy), (ox, oy), size in leaves:
            closed = math.degrees(math.atan2(oy - hy, ox - hx)) % 360
            opened = math.degrees(math.atan2(ty, tx)) % 360
            start = closed if (opened - closed) % 360 == 90 else opened
            box = (hx - size, hy - size, hx + size, hy + size)
            self.draw_base.arc(
                box, start, start + 90, fill=colour(style.swing), width=max(1, width // 2)
            )
            self.draw_base.line(
                (hx, hy, hx + tx * size, hy + ty * size), fill=leaf_colour, width=width
            )

    def _labels(self) -> None:
        size = max(8, round(self.cell * 0.7))
        try:
            font: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.load_default(size)
        except OSError, ImportError:  # pragma: no cover - Pillow without FreeType
            font = ImageFont.load_default()
        fill = colour(self.theme.label)
        for room in self.floor.rooms:
            if len(room.cells) < 6:
                continue
            cx = sum(c.x for c in room.cells) / len(room.cells)
            cy = sum(c.y for c in room.cells) / len(room.cells)
            anchor = min(room.cells, key=lambda c: (c.x + 0.5 - cx) ** 2 + (c.y + 0.5 - cy) ** 2)
            x, y = self.px(anchor.x + 0.5, anchor.y + 0.5)
            text = room.type.replace("_", " ")
            self.draw_base.text(
                (x, y),
                text,
                fill=fill,
                font=font,
                anchor="mm",
                stroke_width=2,
                stroke_fill=(0, 0, 0, 200),
            )

    def _grid(self) -> None:
        step = self.options.grid
        fill = colour(self.theme.grid)
        width = max(1, round(self.cell * 0.02))
        for x in range(0, self.building.width + 2 * self.options.padding + 1, step):
            px = x * self.cell
            self.draw_base.line((px, 0, px, self.size[1]), fill=fill, width=width)
        for y in range(0, self.building.height + 2 * self.options.padding + 1, step):
            py = y * self.cell
            self.draw_base.line((0, py, self.size[0], py), fill=fill, width=width)

    def _compose(self) -> Image.Image:
        radius = self.cell * self.theme.glow_radius
        base = self.image
        if radius <= 0:
            return base
        factor = 4
        small = self.glow.resize(
            (max(1, self.size[0] // factor), max(1, self.size[1] // factor)),
            Image.Resampling.BILINEAR,
        )
        blurred = small.filter(ImageFilter.GaussianBlur(radius / factor))
        halo = blurred.resize(self.size, Image.Resampling.BILINEAR)
        return ImageChops.screen(ImageChops.screen(base, halo), halo)
