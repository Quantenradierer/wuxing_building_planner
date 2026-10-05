"""Procedural object shapes for the image renderer.

Each shape draws one object into its pixel box. `back` is the side against the wall (the
opposite of the object's facing). Shapes draw on the base image and may add neon marks to
the glow layer.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass

from PIL import ImageDraw

from roomplanner.geometry import Side
from roomplanner.render.theme import Colour, ObjectStyle, colour

type Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Pen:
    base: ImageDraw.ImageDraw
    glow: ImageDraw.ImageDraw
    cell: float  # pixels per cell
    style: ObjectStyle

    @property
    def fill(self) -> Colour:
        return colour(self.style.fill)

    @property
    def stroke(self) -> Colour:
        return colour(self.style.stroke)

    @property
    def detail(self) -> Colour:
        return colour(self.style.detail)

    @property
    def line(self) -> int:
        return max(1, round(self.cell * 0.05))

    def glow_colour(self) -> Colour | None:
        return colour(self.style.glow) if self.style.glow else None


def inset(box: Box, amount: float) -> Box:
    x0, y0, x1, y1 = box
    amount = min(amount, (x1 - x0) / 3, (y1 - y0) / 3)
    return x0 + amount, y0 + amount, x1 - amount, y1 - amount


def strip(box: Box, side: Side, fraction: float) -> Box:
    """The part of the box along one side, `fraction` of its extent deep."""
    x0, y0, x1, y1 = box
    match side:
        case Side.N:
            return x0, y0, x1, y0 + (y1 - y0) * fraction
        case Side.S:
            return x0, y1 - (y1 - y0) * fraction, x1, y1
        case Side.W:
            return x0, y0, x0 + (x1 - x0) * fraction, y1
        case Side.E:
            return x1 - (x1 - x0) * fraction, y0, x1, y1


def _rounded(pen: Pen, box: Box, radius: float = 0.15, fill: Colour | None = None) -> None:
    pen.base.rounded_rectangle(
        box, radius=pen.cell * radius, fill=fill or pen.fill, outline=pen.stroke, width=pen.line
    )


def box_shape(pen: Pen, box: Box, facing: Side) -> None:
    _rounded(pen, inset(box, pen.cell * 0.08), radius=0.08)


def rounded_shape(pen: Pen, box: Box, facing: Side) -> None:
    _rounded(pen, inset(box, pen.cell * 0.1), radius=0.3)


def circle_shape(pen: Pen, box: Box, facing: Side) -> None:
    inner = inset(box, pen.cell * 0.1)
    pen.base.ellipse(inner, fill=pen.fill, outline=pen.stroke, width=pen.line)
    pen.base.ellipse(inset(inner, pen.cell * 0.18), outline=pen.detail, width=pen.line)


def desk_shape(pen: Pen, box: Box, facing: Side) -> None:
    inner = inset(box, pen.cell * 0.1)
    _rounded(pen, inner, radius=0.08)
    # Monitor along the back edge, glowing.
    monitor = inset(strip(inner, facing.opposite, 0.28), pen.cell * 0.2)
    pen.base.rectangle(monitor, fill=pen.detail)
    if (glow := pen.glow_colour()) is not None:
        pen.glow.rectangle(monitor, fill=glow)


def cubicle_shape(pen: Pen, box: Box, facing: Side) -> None:
    """Partitions on the back and both flanks, open at the front; the desk along the back."""
    wall = pen.cell * 0.14
    x0, y0, x1, y1 = box
    flanks = (Side.W, Side.E) if facing in (Side.N, Side.S) else (Side.N, Side.S)
    for side in (facing.opposite, *flanks):
        extent = x1 - x0 if side in (Side.W, Side.E) else y1 - y0
        panel = strip(box, side, wall / extent)
        pen.base.rectangle(panel, fill=pen.fill, outline=pen.stroke, width=pen.line)
    inner = (x0 + wall, y0 + wall, x1 - wall, y1 - wall)
    depth = y1 - y0 - 2 * wall if facing in (Side.N, Side.S) else x1 - x0 - 2 * wall
    desk = strip(inner, facing.opposite, pen.cell * 0.9 / depth)
    pen.base.rectangle(desk, fill=pen.detail, outline=pen.stroke, width=pen.line)
    monitor = inset(strip(desk, facing.opposite, 0.35), pen.cell * 0.3)
    pen.base.rectangle(monitor, fill=pen.stroke)
    if (glow := pen.glow_colour()) is not None:
        pen.glow.rectangle(monitor, fill=glow)


def bed_shape(pen: Pen, box: Box, facing: Side) -> None:
    inner = inset(box, pen.cell * 0.08)
    _rounded(pen, inner, radius=0.12)
    pillow = inset(strip(inner, facing.opposite, 0.22), pen.cell * 0.12)
    pen.base.rounded_rectangle(pillow, radius=pen.cell * 0.1, fill=pen.detail)
    blanket = strip(inner, facing, 0.6)
    pen.base.rectangle(inset(blanket, pen.line), fill=pen.stroke)


def _arrow(pen: Pen, tail: tuple[float, float], head: tuple[float, float]) -> None:
    """A direction arrow from `tail` to `head` (a shaft and a filled head)."""
    dx, dy = head[0] - tail[0], head[1] - tail[1]
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    size = min(pen.cell * 0.45, length / 2)
    base = (head[0] - ux * size, head[1] - uy * size)
    pen.base.line((*tail, *base), fill=pen.stroke, width=pen.line * 2)
    wing = size * 0.5
    pen.base.polygon(
        [
            head,
            (base[0] - uy * wing, base[1] + ux * wing),
            (base[0] + uy * wing, base[1] - ux * wing),
        ],
        fill=pen.stroke,
    )


def stairs_shape(pen: Pen, box: Box, facing: Side) -> None:
    """Two flights side by side with a rail between them; the landing lies at the wall
    behind (away from `facing`) when they run towards it, else at the far end. The left
    flight (seen from the landing) has an up arrow, the right one a down arrow."""
    x0, y0, x1, y1 = box
    pen.base.rectangle(box, fill=pen.fill, outline=pen.stroke, width=pen.line)
    along_x = (x1 - x0) >= (y1 - y0)
    step = pen.cell * 0.5
    landing = step * 2
    if along_x:
        start, end = (x0 + landing, x1) if facing is Side.E else (x0, x1 - landing)
        mid = (y0 + y1) / 2
        flat = (x0, y0, start, y1) if facing is Side.E else (end, y0, x1, y1)
        pen.base.rectangle(flat, fill=pen.detail, outline=pen.stroke, width=pen.line)
        x = start + step
        while x < end:
            pen.base.line((x, y0, x, y1), fill=pen.detail, width=pen.line)
            x += step
        gap = pen.line * 2
        pen.base.rectangle((start, mid - gap, end, mid + gap), fill=pen.fill, outline=pen.stroke)
        # the landing is at `towards`: up runs away from it, down towards it
        far, near = (end, start) if facing is Side.E else (start, end)
        lanes = ((y0 + mid) / 2, (mid + y1) / 2)
        margin = pen.cell * 0.4
        away = -margin if far > near else margin
        _arrow(pen, (near - away, lanes[0]), (far + away, lanes[0]))
        _arrow(pen, (far + away, lanes[1]), (near - away, lanes[1]))
    else:
        start, end = (y0 + landing, y1) if facing is Side.S else (y0, y1 - landing)
        mid = (x0 + x1) / 2
        flat = (x0, y0, x1, start) if facing is Side.S else (x0, end, x1, y1)
        pen.base.rectangle(flat, fill=pen.detail, outline=pen.stroke, width=pen.line)
        y = start + step
        while y < end:
            pen.base.line((x0, y, x1, y), fill=pen.detail, width=pen.line)
            y += step
        gap = pen.line * 2
        pen.base.rectangle((mid - gap, start, mid + gap, end), fill=pen.fill, outline=pen.stroke)
        far, near = (end, start) if facing is Side.S else (start, end)
        lanes = ((x0 + mid) / 2, (mid + x1) / 2)
        margin = pen.cell * 0.4
        away = -margin if far > near else margin
        _arrow(pen, (lanes[0], near - away), (lanes[0], far + away))
        _arrow(pen, (lanes[1], far + away), (lanes[1], near - away))


def elevator_shape(pen: Pen, box: Box, facing: Side) -> None:
    inner = inset(box, pen.cell * 0.15)
    pen.base.rectangle(inner, fill=pen.fill, outline=pen.stroke, width=pen.line * 2)
    x0, y0, x1, y1 = inner
    pen.base.line((x0, y0, x1, y1), fill=pen.detail, width=pen.line)
    pen.base.line((x0, y1, x1, y0), fill=pen.detail, width=pen.line)


def wc_shape(pen: Pen, box: Box, facing: Side) -> None:
    inner = inset(box, pen.cell * 0.12)
    tank = strip(inner, facing.opposite, 0.3)
    pen.base.rectangle(tank, fill=pen.stroke)
    bowl = strip(inner, facing, 0.75)
    pen.base.ellipse(inset(bowl, pen.cell * 0.05), fill=pen.fill, outline=pen.stroke)


def sink_shape(pen: Pen, box: Box, facing: Side) -> None:
    inner = inset(box, pen.cell * 0.1)
    pen.base.rounded_rectangle(inner, radius=pen.cell * 0.1, fill=pen.fill, outline=pen.stroke)
    pen.base.ellipse(inset(inner, pen.cell * 0.12), outline=pen.detail, width=pen.line)


def shelf_shape(pen: Pen, box: Box, facing: Side) -> None:
    inner = inset(box, pen.cell * 0.06)
    pen.base.rectangle(inner, fill=pen.fill, outline=pen.stroke, width=pen.line)
    x0, y0, x1, y1 = inner
    along_x = (x1 - x0) >= (y1 - y0)
    count = max(2, round(max(x1 - x0, y1 - y0) / (pen.cell * 0.5)))
    for i in range(1, count):
        if along_x:
            x = x0 + (x1 - x0) * i / count
            pen.base.line((x, y0, x, y1), fill=pen.detail, width=pen.line)
        else:
            y = y0 + (y1 - y0) * i / count
            pen.base.line((x0, y, x1, y), fill=pen.detail, width=pen.line)
    if (glow := pen.glow_colour()) is not None:  # status LEDs
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        r = pen.cell * 0.05
        pen.glow.ellipse((cx - r, cy - r, cx + r, cy + r), fill=glow)


def table_shape(pen: Pen, box: Box, facing: Side) -> None:
    inner = inset(box, pen.cell * 0.2)
    _rounded(pen, inner, radius=0.25)
    pen.base.rounded_rectangle(
        inset(inner, pen.cell * 0.15), radius=pen.cell * 0.2, outline=pen.detail, width=pen.line
    )


def seat_shape(pen: Pen, box: Box, facing: Side) -> None:
    """Sofa or armchair: backrest along the wall, arms at the ends."""
    inner = inset(box, pen.cell * 0.1)
    _rounded(pen, inner, radius=0.25)
    back = strip(inner, facing.opposite, 0.3)
    pen.base.rounded_rectangle(back, radius=pen.cell * 0.15, fill=pen.stroke)
    ends = (Side.W, Side.E) if facing in (Side.N, Side.S) else (Side.N, Side.S)
    for end in ends:
        arm = strip(inner, end, 0.2 if (inner[2] - inner[0]) > pen.cell * 2.5 else 0.3)
        pen.base.rounded_rectangle(arm, radius=pen.cell * 0.12, fill=pen.stroke)


def crate_shape(pen: Pen, box: Box, facing: Side) -> None:
    inner = inset(box, pen.cell * 0.1)
    pen.base.rectangle(inner, fill=pen.fill, outline=pen.stroke, width=pen.line * 2)
    x0, y0, x1, y1 = inner
    pen.base.line((x0, y0, x1, y1), fill=pen.detail, width=pen.line)
    pen.base.line((x0, y1, x1, y0), fill=pen.detail, width=pen.line)


def screen_shape(pen: Pen, box: Box, facing: Side) -> None:
    inner = inset(box, pen.cell * 0.15)
    pen.base.rectangle(inner, fill=pen.fill, outline=pen.stroke, width=pen.line)
    screen = inset(inner, pen.cell * 0.1)
    pen.base.rectangle(screen, fill=pen.detail)
    if (glow := pen.glow_colour()) is not None:
        pen.glow.rectangle(screen, fill=glow)


def counter_shape(pen: Pen, box: Box, facing: Side) -> None:
    inner = inset(box, pen.cell * 0.06)
    pen.base.rectangle(inner, fill=pen.fill, outline=pen.stroke, width=pen.line)
    edge = strip(inner, facing, 0.2)
    pen.base.rectangle(edge, fill=pen.detail)


def machine_shape(pen: Pen, box: Box, facing: Side) -> None:
    inner = inset(box, pen.cell * 0.1)
    pen.base.rectangle(inner, fill=pen.fill, outline=pen.stroke, width=pen.line * 2)
    x0, y0, x1, y1 = inner
    r = min(x1 - x0, y1 - y0) * 0.3
    for cx in ((x0 + x1) / 2 - r * 1.2, (x0 + x1) / 2 + r * 1.2):
        cy = (y0 + y1) / 2
        pen.base.ellipse((cx - r, cy - r, cx + r, cy + r), outline=pen.detail, width=pen.line)
    if (glow := pen.glow_colour()) is not None:
        pen.glow.rectangle(strip(inner, facing, 0.08), fill=glow)


def rug_shape(pen: Pen, box: Box, facing: Side) -> None:
    inner = inset(box, pen.cell * 0.15)
    pen.base.rectangle(inner, fill=pen.fill)
    pen.base.rectangle(inset(inner, pen.cell * 0.15), outline=pen.detail, width=pen.line)


def rubble_shape(pen: Pen, box: Box, facing: Side) -> None:
    """Irregular chunks, deterministic from the box position."""
    x0, y0, x1, y1 = box
    rng = random.Random(f"{x0:.0f}:{y0:.0f}")
    chunks = max(3, round((x1 - x0) * (y1 - y0) / (pen.cell * pen.cell) * 3))
    for i in range(chunks):
        cx, cy = rng.uniform(x0, x1), rng.uniform(y0, y1)
        r = pen.cell * rng.uniform(0.12, 0.35)
        points = [
            (
                min(x1, max(x0, cx + r * math.cos(a))),
                min(y1, max(y0, cy + r * math.sin(a))),
            )
            for a in sorted(rng.uniform(0, 2 * math.pi) for _ in range(5))
        ]
        fill = pen.fill if i % 3 else pen.detail
        pen.base.polygon(points, fill=fill, outline=pen.stroke)


def hatch_shape(pen: Pen, box: Box, facing: Side) -> None:
    inner = inset(box, pen.cell * 0.2)
    pen.base.rectangle(inner, fill=pen.fill, outline=pen.stroke, width=pen.line * 2)
    x0, y0, x1, y1 = inner
    step = (x1 - x0) / 4
    for i in range(1, 4):
        pen.base.line((x0 + step * i, y0, x0 + step * i, y1), fill=pen.detail, width=pen.line)


SHAPES: dict[str, Callable[[Pen, Box, Side], None]] = {
    "box": box_shape,
    "rounded": rounded_shape,
    "circle": circle_shape,
    "desk": desk_shape,
    "bed": bed_shape,
    "stairs": stairs_shape,
    "elevator": elevator_shape,
    "wc": wc_shape,
    "cubicle": cubicle_shape,
    "sink": sink_shape,
    "shelf": shelf_shape,
    "table": table_shape,
    "seat": seat_shape,
    "crate": crate_shape,
    "screen": screen_shape,
    "counter": counter_shape,
    "machine": machine_shape,
    "rug": rug_shape,
    "rubble": rubble_shape,
    "hatch": hatch_shape,
}
