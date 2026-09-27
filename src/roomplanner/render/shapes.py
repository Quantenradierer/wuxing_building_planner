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


def bed_shape(pen: Pen, box: Box, facing: Side) -> None:
    inner = inset(box, pen.cell * 0.08)
    _rounded(pen, inner, radius=0.12)
    pillow = inset(strip(inner, facing.opposite, 0.22), pen.cell * 0.12)
    pen.base.rounded_rectangle(pillow, radius=pen.cell * 0.1, fill=pen.detail)
    blanket = strip(inner, facing, 0.6)
    pen.base.rectangle(inset(blanket, pen.line), fill=pen.stroke)


def stairs_shape(pen: Pen, box: Box, facing: Side) -> None:
    """Two flights side by side; the landing between them lies at the wall behind (away
    from `facing`) when they run towards it, else at the far end."""
    x0, y0, x1, y1 = box
    pen.base.rectangle(box, fill=pen.fill, outline=pen.stroke, width=pen.line)
    along_x = (x1 - x0) >= (y1 - y0)
    step = pen.cell * 0.5
    landing = step * 2
    if along_x:
        start, end = (x0 + landing, x1) if facing is Side.E else (x0, x1 - landing)
        mid = (y0 + y1) / 2
        pen.base.line((start, mid, end, mid), fill=pen.stroke, width=pen.line * 2)
        x = start + step
        while x < end:
            pen.base.line((x, y0, x, y1), fill=pen.detail, width=pen.line)
            x += step
    else:
        start, end = (y0 + landing, y1) if facing is Side.S else (y0, y1 - landing)
        mid = (x0 + x1) / 2
        pen.base.line((mid, start, mid, end), fill=pen.stroke, width=pen.line * 2)
        y = start + step
        while y < end:
            pen.base.line((x0, y, x1, y), fill=pen.detail, width=pen.line)
            y += step


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


def urinal_shape(pen: Pen, box: Box, facing: Side) -> None:
    """A narrow wall bowl: backplate on the wall, a small bowl in front (unlike a wc)."""
    inner = inset(box, pen.cell * 0.15)
    pen.base.rectangle(strip(inner, facing.opposite, 0.2), fill=pen.stroke)
    bowl = inset(strip(inner, facing, 0.7), pen.cell * 0.08)
    pen.base.rounded_rectangle(bowl, radius=pen.cell * 0.12, fill=pen.fill, outline=pen.stroke)


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
    "urinal": urinal_shape,
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
