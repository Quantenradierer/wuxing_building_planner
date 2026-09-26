"""Lights: a ceiling grid per room, accent lights against walls, lights outside entrances.

Rules come from `data/lights.yaml`. Later layers change them: security adds floodlights,
condition makes lights flicker or go dark.
"""

from __future__ import annotations

import math
import random
from dataclasses import replace

from roomplanner.geometry import Axis, Cell, Edge, Side
from roomplanner.layer_rules import LightRule, load_lighting
from roomplanner.model import Floor, Light, Opening, OpeningKind, Room
from roomplanner.pipeline.base import Context
from roomplanner.pipeline.registry import register


@register("lights", "rules")
class RulesLighting:
    def apply(self, ctx: Context, floors: list[Floor]) -> list[Floor]:
        table = load_lighting()
        lit: list[Floor] = []
        for floor in floors:
            rng = ctx.rng(f"lights:{floor.level}")
            lights: list[Light] = []
            for room in floor.rooms:
                if (rule := table.ceiling_for(room.type)) is not None:
                    lights += ceiling_grid(room, rule)
                for accent in table.accents.get(room.type, []):
                    if (cell := wall_cell(floor, room, rng)) is not None:
                        lights.append(light_at(accent, cell.x + 0.5, cell.y + 0.5, room.id))
            for door in floor.openings:
                if door.entrance is not None and (rule := table.entrances.get(door.entrance)):
                    x, y = outside(door, 1.0)
                    lights.append(light_at(rule, x, y, None))
            lit.append(replace(floor, lights=(*floor.lights, *lights)))
        return lit


def light_at(rule: LightRule, x: float, y: float, room: str | None) -> Light:
    return Light(rule.kind, x, y, rule.radius, rule.colour, rule.intensity, "on", room)


def ceiling_grid(room: Room, rule: LightRule) -> list[Light]:
    """Lights on a grid of about `spacing` cells over the room; at least one."""
    xs, ys = [c.x for c in room.cells], [c.y for c in room.cells]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs) + 1, max(ys) + 1
    nx = max(1, round((x1 - x0) / rule.spacing))
    ny = max(1, round((y1 - y0) / rule.spacing))
    lights: list[Light] = []
    for i in range(nx):
        for j in range(ny):
            x = x0 + (x1 - x0) * (i + 0.5) / nx
            y = y0 + (y1 - y0) * (j + 0.5) / ny
            if Cell(math.floor(x), math.floor(y)) in room.cells:
                lights.append(light_at(rule, x, y, room.id))
    if not lights:
        cx, cy = sum(xs) / len(xs) + 0.5, sum(ys) / len(ys) + 0.5
        cell = min(room.cells, key=lambda c: ((c.x + 0.5 - cx) ** 2 + (c.y + 0.5 - cy) ** 2, c))
        lights.append(light_at(rule, cell.x + 0.5, cell.y + 0.5, room.id))
    return lights


def wall_cell(floor: Floor, room: Room, rng: random.Random) -> Cell | None:
    """A random cell of the room against a wall."""
    cells = sorted({c for c in room.cells for side in Side if Edge.of(c, side) in floor.walls})
    return rng.choice(cells) if cells else None


def outside(door: Opening, distance: float) -> tuple[float, float]:
    """Point `distance` cells outside an exterior door (doors open outwards)."""
    assert door.kind is OpeningKind.DOOR and door.swing is not None
    first, last = door.edges[0], door.edges[-1]
    if first.axis is Axis.H:
        x, y = (first.x + last.x + 1) / 2, float(first.y)
    else:
        x, y = float(first.x), (first.y + last.y + 1) / 2
    dx, dy = door.swing.towards.delta
    return x + dx * distance, y + dy * distance
