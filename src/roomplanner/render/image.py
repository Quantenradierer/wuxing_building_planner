"""Image renderer: one raster image per floor, drawn from the model (i.e. the JSON) only.

Layers, bottom to top: background, floors with patterns and grain, soft shadows of walls
and objects, objects, walls with windows and doors, room labels, grid. Neon marks (accent
strips, window glass, screens, lights) go to a glow layer that is blurred and added on top.
Objects with a sprite in the theme are drawn as that picture, rotated to their facing; their
shadow follows the sprite's outline. `<kind>.<wealth>.png` variants are preferred for the
object's wealth tier (or the next tier towards middle) over the plain `<kind>.png`.
A `floor.<material>.png` in the sprite directory tiles the floors of that material, aligned to
the building grid so neighbouring rooms of the same material continue the texture.
The picture is drawn at a higher resolution and scaled down for anti-aliasing.
"""

# Pillow's stubs mention numpy types, which are unknown without numpy installed.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from functools import cache, cached_property
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from roomplanner.geometry import Axis, Cell, Edge, Side
from roomplanner.model import (
    Building,
    Floor,
    Opening,
    OpeningKind,
    OpeningState,
    PlacedObject,
    Room,
)
from roomplanner.params import Wealth
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
    lighting: bool = True  # bake the light map (dim unlit areas); off for VTTs that light


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
        self.light: Image.Image | None = None

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
        self._decals()
        self._accents()
        self._object_shadows()
        self._walls_shadow()
        self._apply_shadow()
        self._objects()
        self._walls()
        self._openings()
        self._devices()
        if self.options.lighting:
            self._lights()
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
        if (texture := self._floor_texture(self.theme.material_name(room.type))) is not None:
            self._textured_floor(room, texture)
            return
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

    def _floor_texture(self, material: str) -> Image.Image | None:
        """The material's floor texture scaled to its span, if the theme's sprites have one."""
        directory = self.theme.sprites
        if directory is None or f"floor.{material}" not in _sprites(directory):
            return None
        side = self.theme.materials[material].texture_cells * self.cell
        return _scaled_texture(directory, f"floor.{material}", side)

    def _textured_floor(self, room: Room, texture: Image.Image) -> None:
        x0 = min(c.x for c in room.cells)
        y0 = min(c.y for c in room.cells)
        x1 = max(c.x for c in room.cells) + 1
        y1 = max(c.y for c in room.cells) + 1
        mask = Image.new("L", ((x1 - x0) * self.cell, (y1 - y0) * self.cell), 0)
        draw = ImageDraw.Draw(mask)
        for cell in room.cells:
            cx, cy = (cell.x - x0) * self.cell, (cell.y - y0) * self.cell
            draw.rectangle((cx, cy, cx + self.cell - 1, cy + self.cell - 1), fill=255)
        # Tile from the building grid's texture origin so neighbouring rooms line up.
        side = texture.width
        left, top = self.px(x0, y0)
        ox, oy = round(left) - round(left) % side, round(top) - round(top) % side
        area = Image.new("RGB", mask.size)
        for ty in range(oy, round(top) + mask.height, side):
            for tx in range(ox, round(left) + mask.width, side):
                area.paste(texture, (tx - round(left), ty - round(top)))
        self.image.paste(area, (round(left), round(top)), mask)

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
            if (sprite := self._sprite(obj)) is not None:
                shade = sprite.getchannel("A").point([v * 200 // 255 for v in range(256)])
                self.shadow.paste(shade, (round(x0 + shift), round(y0 + shift)), shade)
                continue
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
            x0, y0 = self.px(obj.x, obj.y)
            if (sprite := self._sprite(obj)) is not None:
                self.image.paste(sprite, (round(x0), round(y0)), sprite)
                continue
            style = self.theme.object_style(obj.kind)
            shape = SHAPES.get(style.shape, SHAPES["box"])
            x1, y1 = self.px(obj.x + obj.w, obj.y + obj.h)
            shape(
                Pen(self.draw_base, self.draw_glow, self.cell, style), (x0, y0, x1, y1), obj.facing
            )

    def _sprite(self, obj: PlacedObject) -> Image.Image | None:
        """The object's sprite turned to its facing and scaled to its box, if it has one."""
        directory = self.theme.sprites
        if directory is None:
            return None
        tier = obj.wealth or self.building.params.wealth
        sprites = _sprites(directory)
        name = sprite_name(sprites, obj.kind, tier)
        if name is None and (stand_in := self.theme.sprite_fallbacks.get(obj.kind)):
            name = sprite_name(sprites, stand_in, tier)
        if name is None:
            return None
        return _placed_sprite(directory, name, obj.facing, obj.w * self.cell, obj.h * self.cell)

    @cached_property
    def _open_air(self) -> frozenset[Cell]:
        """Cells of rooms open to the sky (a balcony)."""
        kinds = set(self.theme.walls.open_air)
        return frozenset(c for r in self.floor.rooms if r.type in kinds for c in r.cells)

    def _is_railing(self, edge: Edge) -> bool:
        """The facade of an open-air room."""
        return self.floor.is_exterior_wall(edge) and any(c in self._open_air for c in edge.cells())

    def _is_exterior(self, edge: Edge) -> bool:
        if self.floor.is_exterior_wall(edge):
            return True
        a, b = edge.cells()  # indoors against the open air
        return (a in self._open_air) != (b in self._open_air)

    def _thickness(self, edge: Edge) -> float:
        walls = self.theme.walls
        if self._is_railing(edge):
            return self.cell * walls.railing
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
            match opening.kind:
                case OpeningKind.WINDOW:
                    self._window(opening)
                case OpeningKind.BREACH:
                    self._breach(opening)
                case OpeningKind.DOOR:
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
        if opening.state is OpeningState.BROKEN:
            # Shattered: dull glass stubs at both ends, shards on the floor.
            rng = random.Random(f"{self.building.seed}:{opening.edges[0]}")
            shard = colour(style.glass)
            dull = (*shard[:3], 110)
            for _ in range(3 * len(opening.edges)):
                x = rng.uniform(ax, bx) if horizontal else ax + rng.uniform(-3, 3) * half
                y = ay + rng.uniform(-3, 3) * half if horizontal else rng.uniform(ay, by)
                r = self.cell * rng.uniform(0.03, 0.08)
                self.draw_base.polygon(
                    [(x - r, y), (x, y - r * 1.5), (x + r, y + r * 0.5)], fill=dull
                )
            return
        self.draw_base.rectangle(glass, fill=colour(style.glass))
        self.draw_glow.rectangle(glass, fill=colour(style.glow)[:3])

    def _breach(self, opening: Opening) -> None:
        """Ragged hole: rubble chunks at the torn wall ends and spilled on both sides."""
        rng = random.Random(f"{self.building.seed}:{opening.edges[0]}:breach")
        (ax, ay), (bx, by) = self._run(opening)
        half = self._thickness(opening.edges[0]) / 2
        horizontal = opening.axis is Axis.H
        rubble = colour(self.theme.condition.rubble)
        wall = colour(self.theme.walls.colour)
        for _ in range(10 * len(opening.edges)):
            t = rng.random()
            spread = rng.uniform(-4, 4) * half
            x = ax + (bx - ax) * t + (0 if horizontal else spread)
            y = ay + (by - ay) * t + (spread if horizontal else 0)
            r = self.cell * rng.uniform(0.05, 0.16)
            points = [
                (x + r * math.cos(a), y + r * math.sin(a))
                for a in sorted(rng.uniform(0, 2 * math.pi) for _ in range(5))
            ]
            self.draw_base.polygon(points, fill=rng.choice([rubble, wall]))

    def _door(self, opening: Opening) -> None:
        style = self.theme.doors
        swing = opening.swing
        if swing is None:
            return
        exterior = self._is_exterior(opening.edges[0])
        leaf = style.exterior_leaf if exterior and style.exterior_leaf else style.leaf
        if opening.material is not None and opening.material in style.materials:
            leaf = style.materials[opening.material]
        leaf_colour = colour(leaf)
        (ax, ay), (bx, by) = self._run(opening)
        length = math.hypot(bx - ax, by - ay)
        tx, ty = swing.towards.delta
        width = max(2, round(self.cell * 0.09))
        if opening.material in ("security", "blast"):
            width = round(width * 1.6)
        if opening.lock is not None and opening.lock in style.locks:
            self._lock_marker(opening, style.locks[opening.lock])
        match opening.state:
            case OpeningState.MISSING:
                return
            case OpeningState.BLOCKED:
                self._barricade(opening)
                return
            case OpeningState.BROKEN:
                # The leaf hangs askew, half open, without a swing arc.
                hx, hy = (ax, ay) if swing.hinge in (Side.W, Side.N) else (bx, by)
                ox, oy = (bx, by) if (hx, hy) == (ax, ay) else (ax, ay)
                mx, my = (hx + ox) / 2 + tx * length * 0.35, (hy + oy) / 2 + ty * length * 0.35
                self.draw_base.line((hx, hy, mx, my), fill=leaf_colour, width=width)
                return
            case OpeningState.INTACT:
                pass
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

    def _lock_marker(self, opening: Opening, lock_colour: str) -> None:
        """A small glowing box on the jamb opposite the hinge, on the swing side."""
        assert opening.swing is not None
        (ax, ay), (bx, by) = self._run(opening)
        jx, jy = (bx, by) if opening.swing.hinge in (Side.W, Side.N) else (ax, ay)
        tx, ty = opening.swing.towards.delta
        half = self._thickness(opening.edges[0]) / 2 + self.cell * 0.08
        x, y = jx + tx * half, jy + ty * half
        r = self.cell * 0.09
        fill = colour(lock_colour)
        self.draw_base.rectangle((x - r, y - r, x + r, y + r), fill=fill, outline=(0, 0, 0, 255))
        self.draw_glow.rectangle((x - r, y - r, x + r, y + r), fill=fill[:3])

    def _barricade(self, opening: Opening) -> None:
        """Planks nailed across the doorway."""
        (ax, ay), (bx, by) = self._run(opening)
        half = self._thickness(opening.edges[0]) / 2 + self.cell * 0.1
        plank = colour(self.theme.doors.barricade)
        width = max(2, round(self.cell * 0.14))
        if opening.axis is Axis.H:
            lines = [(ax, ay - half, bx, by + half), (ax, ay + half, bx, by - half)]
        else:
            lines = [(ax - half, ay, bx + half, by), (ax + half, ay, bx - half, by)]
        for line in lines:
            self.draw_base.line(line, fill=(0, 0, 0, 255), width=width + 2)
            self.draw_base.line(line, fill=plank, width=width)

    def _devices(self) -> None:
        for device in self.floor.devices:
            fill = colour(self.theme.devices.get(device.kind, "#ff3b30"))
            x, y = self.px(device.x + 0.5, device.y + 0.5)
            r = self.cell * 0.18
            if device.kind == "camera":
                dx, dy = device.facing.delta
                # Wedge from the corner towards the view direction.
                tip = (x + dx * r * 2.2, y + dy * r * 2.2)
                side = (-dy * r, dx * r)
                body = [
                    (x + side[0], y + side[1]),
                    (x - side[0], y - side[1]),
                    tip,
                ]
                self.draw_base.polygon(body, fill=(30, 32, 40, 255), outline=fill)
                self.draw_glow.ellipse((x - r / 2, y - r / 2, x + r / 2, y + r / 2), fill=fill[:3])
            else:
                self.draw_base.rounded_rectangle(
                    (x - r, y - r, x + r, y + r), radius=r / 3, fill=(30, 32, 40, 255), outline=fill
                )
                self.draw_glow.ellipse((x - r / 3, y - r / 3, x + r / 3, y + r / 3), fill=fill[:3])

    def _lights(self) -> None:
        """Light map on a low-resolution layer: dims unlit areas, tints lit ones."""
        if self.theme.ambient >= 1 and self.theme.light_strength <= 0:
            return
        factor = 8
        size = (max(1, self.size[0] // factor), max(1, self.size[1] // factor))
        layer = Image.new("RGB", size, (0, 0, 0))
        draw = ImageDraw.Draw(layer)
        for light in sorted(self.floor.lights, key=lambda li: li.intensity):
            if light.state == "off":
                continue
            level = light.intensity * (0.5 if light.state == "flicker" else 1.0)
            r, g, b, _ = colour(light.colour)
            x, y = self.px(light.x, light.y)
            radius = light.radius * self.cell * 0.5 / factor
            cx, cy = x / factor, y / factor
            fill = (round(r * level), round(g * level), round(b * level))
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=fill)
        blurred = layer.filter(ImageFilter.GaussianBlur(self.cell * 1.5 / factor))
        self.light = blurred.resize(self.size, Image.Resampling.BILINEAR)

    def _decals(self) -> None:
        """Stains and graffiti, denser the worse the building's condition."""
        style = self.theme.condition
        density = style.decals.get(self.building.params.condition.value, 0.0)
        if density <= 0:
            return
        rng = random.Random(f"{self.building.seed}:{self.floor.level}:decals")
        cells = sorted(self.floor.footprint)
        stain = colour(style.stain)
        for _ in range(round(len(cells) * density / 40)):
            c = rng.choice(cells)
            x, y = self.px(c.x + rng.random(), c.y + rng.random())
            r = self.cell * rng.uniform(0.4, 1.6)
            self.draw_base.ellipse(
                (x - r, y - r * rng.uniform(0.5, 1), x + r, y + r * rng.uniform(0.5, 1)),
                fill=stain,
            )
        # Graffiti: neon scribbles along walls.
        wall_cells = sorted(
            {c for e in self.floor.walls for c in e.cells() if c in self.floor.footprint}
        )
        width = max(1, round(self.cell * 0.06))
        for _ in range(round(len(wall_cells) * density / 25)):
            c = rng.choice(wall_cells)
            paint = colour(rng.choice(style.graffiti))
            x, y = self.px(c.x + 0.5, c.y + 0.5)
            points = [(x, y)]
            for _ in range(rng.randint(4, 9)):
                x += rng.uniform(-0.5, 0.5) * self.cell
                y += rng.uniform(-0.5, 0.5) * self.cell
                points.append((x, y))
            self.draw_base.line(points, fill=(*paint[:3], 170), width=width, joint="curve")

    def _labels(self) -> None:
        size = max(8, round(self.cell * 0.7))
        try:
            font: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.load_default(size)
        except OSError, ImportError:  # pragma: no cover - Pillow without FreeType
            font = ImageFont.load_default()
        fill = colour(self.theme.label)
        for room in self.floor.rooms:
            if len(room.cells) < 12:  # stalls, tiny closets: the name wouldn't fit
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
            return self._apply_light(base)
        factor = 4
        small = self.glow.resize(
            (max(1, self.size[0] // factor), max(1, self.size[1] // factor)),
            Image.Resampling.BILINEAR,
        )
        blurred = small.filter(ImageFilter.GaussianBlur(radius / factor))
        halo = blurred.resize(self.size, Image.Resampling.BILINEAR)
        result = ImageChops.screen(ImageChops.screen(base, halo), halo)
        return self._apply_light(result)

    def _apply_light(self, image: Image.Image) -> Image.Image:
        if self.light is None:
            return image
        ambient = self.theme.ambient
        # Brightness factor per pixel: ambient where dark, 1 where fully lit.
        grey = self.light.convert("L").point(
            [round(255 * (ambient + (1 - ambient) * min(1.0, v / 160))) for v in range(256)]
        )
        shaded = ImageChops.multiply(image, Image.merge("RGB", (grey, grey, grey)))
        tint = self.light.point([round(v * self.theme.light_strength) for v in range(256)] * 3)
        return ImageChops.screen(shaded, tint)


# Sprites face S (back at the top); degrees counter-clockwise to turn them to a facing.
_SPRITE_TURN = {Side.S: 0, Side.E: 90, Side.N: 180, Side.W: 270}


@cache
def _sprites(directory: str) -> dict[str, Image.Image]:
    return {path.stem: Image.open(path).convert("RGBA") for path in Path(directory).glob("*.png")}


def sprite_name(sprites: dict[str, Image.Image], kind: str, tier: Wealth) -> str | None:
    """`<kind>.<tier>`, else the variants of the tiers towards middle, else plain `<kind>`.

    Luxury falls back to high, squatter to low: an extreme tier never borrows the other
    extreme's look, and low / high never borrow squatter / luxury.
    """
    tiers = list(Wealth)
    here, middle = tiers.index(tier), tiers.index(Wealth.MIDDLE)
    step = 1 if here < middle else -1
    for index in range(here, middle + step, step):
        if f"{kind}.{tiers[index]}" in sprites:
            return f"{kind}.{tiers[index]}"
    return kind if kind in sprites else None


@cache
def _scaled_texture(directory: str, name: str, side: int) -> Image.Image:
    return _sprites(directory)[name].convert("RGB").resize((side, side), Image.Resampling.LANCZOS)


@cache
def _placed_sprite(directory: str, kind: str, facing: Side, w: int, h: int) -> Image.Image:
    turned = _sprites(directory)[kind].rotate(_SPRITE_TURN[facing], expand=True)
    return turned.resize((w, h), Image.Resampling.LANCZOS)
