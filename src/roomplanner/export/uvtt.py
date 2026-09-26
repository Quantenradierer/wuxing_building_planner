"""Universal VTT (`.dd2vtt`, format 0.3): one file per floor with the image embedded.

Coordinates are in grid squares from the image's top-left corner.

- Walls (`line_of_sight`): every wall without an opening, plus barricaded doors.
- Doors and windows (`portals`): intact ones closed, broken ones open (the format has no
  window type; windows can be opened, so they are portals too). Missing doors and breaches
  are plain gaps.
- Lights: every light that is not off; flickering ones at half intensity.
"""

from __future__ import annotations

import json
import math
from typing import Any

from roomplanner.export.common import (
    ExportOptions,
    Point,
    floor_image,
    opening_segment,
    png_base64,
    runs,
    solid_walls,
)
from roomplanner.model import Building, Floor, OpeningKind, OpeningState
from roomplanner.render.theme import Theme

FORMAT = 0.3
AMBIENT_LIGHT = "ff404048"  # ARGB; dark, the exported lights do the lighting
BAKED_AMBIENT_LIGHT = "ffffffff"  # the image already shows the lighting


def to_uvtt(
    building: Building, floor: Floor, theme: Theme, options: ExportOptions
) -> dict[str, Any]:
    image = floor_image(building, floor, theme, options)
    square = options.cells_per_square
    pad = options.render.padding

    def grid(point: Point) -> dict[str, float]:
        return {"x": (point[0] + pad) / square, "y": (point[1] + pad) / square}

    walls = [[grid(a), grid(b)] for a, b in runs(solid_walls(floor))]
    portals: list[dict[str, Any]] = []
    for door in floor.openings:
        # Windows can be opened (or climbed through when broken): portals like doors.
        if door.kind is OpeningKind.BREACH or door.state is OpeningState.MISSING:
            continue
        a, b = opening_segment(door)
        if door.state is OpeningState.BLOCKED:
            walls.append([grid(a), grid(b)])
            continue
        centre = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        portals.append(
            {
                "position": grid(centre),
                "bounds": [grid(a), grid(b)],
                "rotation": math.atan2(b[1] - a[1], b[0] - a[0]),
                "closed": door.state is OpeningState.INTACT,
                "freestanding": False,
            }
        )
    lights: list[dict[str, Any]] = []
    if options.lights:
        for light in floor.lights:
            if light.state == "off":
                continue
            intensity = light.intensity * (0.5 if light.state == "flicker" else 1.0)
            lights.append(
                {
                    "position": grid((light.x, light.y)),
                    "range": light.radius / square,
                    "intensity": round(intensity, 3),
                    "color": "ff" + light.colour.removeprefix("#")[:6].lower(),
                    "shadows": True,
                }
            )
    width, height = image.size
    return {
        "format": FORMAT,
        "resolution": {
            "map_origin": {"x": 0, "y": 0},
            "map_size": {
                "x": width / options.pixels_per_square,
                "y": height / options.pixels_per_square,
            },
            "pixels_per_grid": options.pixels_per_square,
        },
        "line_of_sight": walls,
        "objects_line_of_sight": [],
        "portals": portals,
        "environment": {
            "baked_lighting": options.baked_lighting,
            "ambient_light": BAKED_AMBIENT_LIGHT if options.baked_lighting else AMBIENT_LIGHT,
        },
        "lights": lights,
        "image": png_base64(image),
    }


def uvtt_json(building: Building, floor: Floor, theme: Theme, options: ExportOptions) -> str:
    return json.dumps(to_uvtt(building, floor, theme, options), separators=(",", ":"))
