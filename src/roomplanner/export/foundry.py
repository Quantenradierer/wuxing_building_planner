"""Foundry VTT (v14+): one scene with one Scene Level per floor, for the Schattenakte module.

The export is a single JSON file (`<name>.schattenakte.json`):

    {"format": "schattenakte", "version": 1, "name": …, "scene": {…}, "images": {file: b64}}

`scene` is Foundry scene data; each level's `background.src` is a file name in `images`.
The Schattenakte module (`foundry-module/`) uploads the images to
`Data/schattenakte/<name>/`, points the levels at them and creates the scene.

Floors are stacked levels `FLOOR_HEIGHT_M` apart (grid units are metres). Walls and lights
belong to their floor's level; levels don't see each other (a building has solid floors).
Every stairwell and elevator is a region spanning the levels it serves, from the bottom of
its lowest floor to just above the bottom of its highest, with the native Change Level
behaviour (the layout of Foundry's own Scene Levels demo).

Walls: plain walls block everything; windows are see-through doors (closed: movement and
sound blocked, can be opened to climb out); doors are doors (locked if they have a lock,
open if broken; elevator doors slide); barricaded doors are walls; missing doors, broken
windows and breaches are gaps.
"""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass
from typing import Any

from PIL import Image

from roomplanner.export.common import (
    ExportOptions,
    Point,
    floor_image,
    opening_segment,
    stable_id,
    wall_segments,
)
from roomplanner.geometry import CELL_SIZE_M, Cell
from roomplanner.i18n import level_name, term
from roomplanner.model import Building, Floor, OpeningKind, OpeningState, Room
from roomplanner.render.theme import Theme

# Foundry constants (CONST.WALL_SENSE_TYPES, WALL_MOVEMENT_TYPES, WALL_DOOR_TYPES/STATES).
SENSE_NONE, SENSE_LIMITED, SENSE_NORMAL = 0, 10, 20
MOVE_NONE, MOVE_NORMAL = 0, 20
DOOR_NONE, DOOR_DOOR = 0, 1
DOOR_CLOSED, DOOR_OPEN, DOOR_LOCKED = 0, 1, 2
SLIDING_DOOR = {"type": "slide", "double": True}  # WallDocument.animation: leaves part

TRANSPORT = {
    "stairwell": "stairs",
    "public_stairs": "stairs",
    "elevator": "elevator_car",
}  # room type -> object kind
FORMAT = "schattenakte"
FORMAT_VERSION = 1
FLOOR_HEIGHT_M = 3.0
STAIR_OVERLAP_M = 1.0  # a shaft region reaches this far into its highest level
GROUND_LEVEL_ID = "defaultLevel0000"  # Foundry's id for a scene's first level
DARKNESS = 0.6  # scene darkness when the VTT does the lighting
WEBP_QUALITY = 85

type Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class FoundryExport:
    name: str
    scene: dict[str, Any]
    images: dict[str, bytes]  # file name -> WebP

    def document(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "version": FORMAT_VERSION,
            "name": self.name,
            "scene": self.scene,
            "images": {
                file: base64.b64encode(data).decode("ascii") for file, data in self.images.items()
            },
        }

    def json(self) -> str:
        return json.dumps(self.document(), separators=(",", ":"))


def to_foundry(
    building: Building, theme: Theme, options: ExportOptions, name: str
) -> FoundryExport:
    """The building as one scene; `name` names the scene and the module's upload folder."""
    level_ids = {f.level: level_id(building, name, f.level) for f in building.floors}
    images: dict[str, bytes] = {}
    levels: list[dict[str, Any]] = []
    walls: list[dict[str, Any]] = []
    lights: list[dict[str, Any]] = []
    size = (0, 0)
    for index, floor in enumerate(sorted(building.floors, key=lambda f: f.level)):
        image = floor_image(building, floor, theme, options)
        size = image.size
        tag = f"F{floor.level}" if floor.level >= 0 else f"B{-floor.level}"
        file = f"{name}_{tag}.webp"
        images[file] = webp_bytes(image)
        bottom, top = elevation(floor.level)
        levels.append(
            {
                "_id": level_ids[floor.level],
                "name": level_name(floor.level, options.language),
                "elevation": {"bottom": bottom, "top": top},
                "background": {"src": file},
                "visibility": {"levels": []},
                "sort": index,
            }
        )
        scene = _Level(building, floor, options, name, level_ids[floor.level])
        walls += scene.walls()
        if options.lights:
            lights += scene.lights()
    scene_data = {
        "_id": stable_id(building.seed, name, "scene"),
        "name": name,
        "navigation": True,
        "width": size[0],
        "height": size[1],
        "padding": 0,
        "grid": {
            "type": 1,
            "size": options.pixels_per_square,
            "distance": options.grid_m,
            "units": "m",
        },
        "tokenVision": True,
        "environment": {"darknessLevel": 0.0 if options.baked_lighting else DARKNESS},
        "levels": levels,
        "initialLevel": level_ids.get(0, levels[0]["_id"]),
        "walls": walls,
        "lights": lights,
        "regions": _shafts(building, options, name, level_ids),
        "flags": {"roomplanner": {"seed": building.seed, "type": building.params.building_type}},
    }
    return FoundryExport(name, scene_data, images)


def level_id(building: Building, name: str, level: int) -> str:
    return GROUND_LEVEL_ID if level == 0 else stable_id(building.seed, name, "level", level)


def elevation(level: int) -> tuple[float, float]:
    return level * FLOOR_HEIGHT_M, (level + 1) * FLOOR_HEIGHT_M


def webp_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=WEBP_QUALITY)
    return buffer.getvalue()


class _Level:
    def __init__(
        self, building: Building, floor: Floor, options: ExportOptions, name: str, level: str
    ) -> None:
        self.building = building
        self.floor = floor
        self.name = name
        self.level = level
        self.cell = options.cell_px
        self.pad = options.render.padding

    def px(self, point: Point) -> tuple[float, float]:
        return (point[0] + self.pad) * self.cell, (point[1] + self.pad) * self.cell

    def _id(self, *parts: object) -> str:
        return stable_id(self.building.seed, self.name, self.floor.level, *parts)

    def walls(self) -> list[dict[str, Any]]:
        walls: list[dict[str, Any]] = []

        def add(a: Point, b: Point, **kind: Any) -> None:
            (x1, y1), (x2, y2) = self.px(a), self.px(b)
            wall = {
                "_id": self._id("wall", len(walls)),
                "c": [x1, y1, x2, y2],
                "move": MOVE_NORMAL,
                "sight": SENSE_NORMAL,
                "light": SENSE_NORMAL,
                "sound": SENSE_NORMAL,
                "door": DOOR_NONE,
                "ds": DOOR_CLOSED,
                "levels": [self.level],
            }
            walls.append(wall | kind)

        for a, b in wall_segments(self.floor):
            add(a, b)
        for opening in self.floor.openings:
            a, b = opening_segment(opening, self.floor)
            match opening.kind, opening.state:
                case OpeningKind.WINDOW, OpeningState.BROKEN:
                    pass  # a hole: nothing stops anyone
                case OpeningKind.WINDOW, _:
                    # See-through, and a door: windows can be opened to climb out.
                    add(
                        a,
                        b,
                        sight=SENSE_NONE,
                        light=SENSE_NONE,
                        sound=SENSE_LIMITED,
                        door=DOOR_DOOR,
                    )
                case OpeningKind.DOOR, OpeningState.BLOCKED:
                    add(a, b)
                case OpeningKind.DOOR, OpeningState.BROKEN | OpeningState.INTACT:
                    if opening.state is OpeningState.BROKEN:
                        state = DOOR_OPEN
                    else:
                        state = DOOR_LOCKED if opening.lock else DOOR_CLOSED
                    slide = {"animation": SLIDING_DOOR} if opening.sliding else {}
                    add(a, b, door=DOOR_DOOR, ds=state, **slide)
                case _:
                    pass  # missing doors and breaches are gaps
        return walls

    def lights(self) -> list[dict[str, Any]]:
        lights: list[dict[str, Any]] = []
        for i, light in enumerate(self.floor.lights):
            if light.state == "off":
                continue
            x, y = self.px((light.x, light.y))
            radius = light.radius * CELL_SIZE_M
            animation: dict[str, Any] = {"type": None}
            if light.state == "flicker":
                animation = {"type": "torch", "speed": 6, "intensity": 6}
            lights.append(
                {
                    "_id": self._id("light", i),
                    "x": x,
                    "y": y,
                    "elevation": elevation(self.floor.level)[0],
                    "levels": [self.level],
                    "config": {
                        "dim": round(radius, 2),
                        "bright": round(radius * 0.4, 2),
                        "color": light.colour,
                        "alpha": round(0.3 * light.intensity, 3),
                        "luminosity": 0.5,
                        "animation": animation,
                    },
                }
            )
        return lights


# --- stairs and elevators -------------------------------------------------------------


def _shafts(
    building: Building, options: ExportOptions, name: str, level_ids: dict[int, str]
) -> list[dict[str, Any]]:
    """One Change Level region per run of consecutive floors sharing a stairwell/elevator."""
    shafts: dict[tuple[str, Cell], list[tuple[Floor, Room]]] = {}
    for floor in sorted(building.floors, key=lambda f: f.level):
        for room in floor.rooms:
            if room.type in TRANSPORT:
                # Core rooms share their cells on all floors.
                shafts.setdefault((room.type, min(room.cells)), []).append((floor, room))
    regions: list[dict[str, Any]] = []
    for key, stops in sorted(shafts.items()):
        for run in _consecutive(stops):
            if len(run) < 2:
                continue
            levels = [f.level for f, _ in run]
            x0, y0, x1, y1 = _footprint(run)
            pad, cell = options.render.padding, options.cell_px
            lang = options.language
            label = term("word", "elevator" if key[0] == "elevator" else "stairs", lang)
            first, last = (level_name(f.level, lang) for f in (run[0][0], run[-1][0]))
            region_id = stable_id(building.seed, name, "region", *key, levels[0])
            regions.append(
                {
                    "_id": region_id,
                    "name": f"{label} ({first} - {last})",
                    "color": "#00e5ff" if key[0] == "elevator" else "#ffb347",
                    "shapes": [
                        {
                            "type": "rectangle",
                            "x": (x0 + pad) * cell,
                            "y": (y0 + pad) * cell,
                            "width": (x1 - x0) * cell,
                            "height": (y1 - y0) * cell,
                            "rotation": 0,
                            "hole": False,
                        }
                    ],
                    "levels": [level_ids[level] for level in levels],
                    "elevation": {
                        "bottom": elevation(levels[0])[0],
                        "top": elevation(levels[-1])[0] + STAIR_OVERLAP_M,
                    },
                    "behaviors": [
                        {
                            "_id": stable_id(region_id, "behavior"),
                            "name": term("word", "change_level", options.language),
                            "type": "changeLevel",
                            "system": {"movementActions": []},
                            "disabled": False,
                        }
                    ],
                }
            )
    return regions


def _consecutive(stops: list[tuple[Floor, Room]]) -> list[list[tuple[Floor, Room]]]:
    groups: list[list[tuple[Floor, Room]]] = []
    for stop in stops:
        if groups and groups[-1][-1][0].level == stop[0].level - 1:
            groups[-1].append(stop)
        else:
            groups.append([stop])
    return groups


def _footprint(run: list[tuple[Floor, Room]]) -> Box:
    """The stairs or car where it is the same on every floor, else the room's bounding box."""
    boxes: set[Box | None] = set()
    for floor, room in run:
        kind = TRANSPORT[room.type]
        vehicle = next((o for o in floor.objects if o.room == room.id and o.kind == kind), None)
        boxes.add(
            None
            if vehicle is None
            else (vehicle.x, vehicle.y, vehicle.x + vehicle.w, vehicle.y + vehicle.h)
        )
    if len(boxes) == 1 and (box := next(iter(boxes))) is not None:
        return box
    xs = [c.x for c in run[0][1].cells]
    ys = [c.y for c in run[0][1].cells]
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1
