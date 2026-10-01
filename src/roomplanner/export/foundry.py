"""Foundry VTT (v12+): one scene per floor, packaged with an import macro.

Output folder:
    <name>_F0.png, <name>_F1.png, …   floor images
    scenes.json                      scene data with stable ids
    import-macro.js                  creates the scenes, keeping the ids

Copy the folder into Foundry's `Data/roomplanner/` and run the macro. The ids must survive
the import because stairs link the floors: each stairwell and elevator gets an "up" and a
"down" region (a Scene Region with a native Teleport Token behaviour) that moves tokens to
the arrival region of the same stairwell on the floor above or below.

Walls: plain walls block everything; windows are see-through doors (closed: movement and
sound blocked, can be opened to climb out); doors are doors (locked if they have a lock,
open if broken); barricaded doors are walls; missing doors, broken windows and breaches
are gaps.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roomplanner.export.common import (
    ExportOptions,
    Point,
    floor_image,
    opening_segment,
    png_bytes,
    runs,
    solid_walls,
    stable_id,
)
from roomplanner.geometry import CELL_SIZE_M, Cell
from roomplanner.model import Building, Floor, OpeningKind, OpeningState, PlacedObject, Room
from roomplanner.render.theme import Theme

# Foundry constants (CONST.WALL_SENSE_TYPES, WALL_MOVEMENT_TYPES, WALL_DOOR_TYPES/STATES).
SENSE_NONE, SENSE_LIMITED, SENSE_NORMAL = 0, 10, 20
MOVE_NONE, MOVE_NORMAL = 0, 20
DOOR_NONE, DOOR_DOOR = 0, 1
DOOR_CLOSED, DOOR_OPEN, DOOR_LOCKED = 0, 1, 2

TRANSPORT = {
    "stairwell": "stairs",
    "public_stairs": "stairs",
    "elevator": "elevator_car",
}  # room type -> object kind
ASSET_ROOT = "roomplanner"
DARKNESS = 0.6  # scene darkness when the VTT does the lighting


@dataclass(frozen=True)
class FoundryExport:
    scenes: list[dict[str, Any]]
    images: dict[str, bytes]  # file name -> PNG
    macro: str

    def write(self, folder: Path) -> list[Path]:
        folder.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for name, data in self.images.items():
            (folder / name).write_bytes(data)
            written.append(folder / name)
        (folder / "scenes.json").write_text(json.dumps(self.scenes, indent=1), encoding="utf-8")
        (folder / "import-macro.js").write_text(self.macro, encoding="utf-8")
        return [*written, folder / "scenes.json", folder / "import-macro.js"]


def to_foundry(
    building: Building, theme: Theme, options: ExportOptions, name: str
) -> FoundryExport:
    """Scenes for all floors; `name` is the folder name under Data/roomplanner/."""
    scene_ids = {f.level: stable_id(building.seed, name, "scene", f.level) for f in building.floors}
    images: dict[str, bytes] = {}
    scenes: list[dict[str, Any]] = []
    for floor in building.floors:
        image = floor_image(building, floor, theme, options)
        tag = f"F{floor.level}" if floor.level >= 0 else f"B{-floor.level}"
        file = f"{name}_{tag}.png"
        images[file] = png_bytes(image)
        scene = _Scene(building, floor, options, name, scene_ids)
        scenes.append(
            {
                "_id": scene_ids[floor.level],
                "name": f"{name} - {floor.name}",
                "navName": floor.name,
                "navigation": True,
                "navOrder": floor.level,
                "width": image.size[0],
                "height": image.size[1],
                "padding": 0,
                "background": {"src": f"{ASSET_ROOT}/{name}/{file}"},
                "grid": {
                    "type": 1,
                    "size": options.pixels_per_square,
                    "distance": options.grid_m,
                    "units": "m",
                },
                "tokenVision": True,
                "fog": {"exploration": True},
                "environment": {"darknessLevel": 0.0 if options.baked_lighting else DARKNESS},
                "walls": scene.walls(),
                "lights": scene.lights() if options.lights else [],
                "regions": scene.regions(),
                "flags": {"roomplanner": {"level": floor.level, "seed": building.seed}},
            }
        )
    return FoundryExport(scenes, images, _macro(name))


class _Scene:
    def __init__(
        self,
        building: Building,
        floor: Floor,
        options: ExportOptions,
        name: str,
        scene_ids: dict[int, str],
    ) -> None:
        self.building = building
        self.floor = floor
        self.options = options
        self.name = name
        self.scene_ids = scene_ids
        self.cell = options.cell_px
        self.pad = options.render.padding

    def px(self, point: Point) -> tuple[float, float]:
        return (point[0] + self.pad) * self.cell, (point[1] + self.pad) * self.cell

    def _id(self, *parts: object) -> str:
        return stable_id(self.building.seed, self.name, self.floor.level, *parts)

    def walls(self) -> list[dict[str, Any]]:
        walls: list[dict[str, Any]] = []

        def add(a: Point, b: Point, **kind: int) -> None:
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
            }
            walls.append(wall | kind)

        for a, b in runs(solid_walls(self.floor)):
            add(a, b)
        for opening in self.floor.openings:
            a, b = opening_segment(opening)
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
                case OpeningKind.DOOR, OpeningState.BROKEN:
                    add(a, b, door=DOOR_DOOR, ds=DOOR_OPEN)
                case OpeningKind.DOOR, OpeningState.INTACT:
                    state = DOOR_LOCKED if opening.lock else DOOR_CLOSED
                    add(a, b, door=DOOR_DOOR, ds=state)
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

    # --- stairs and elevators ---------------------------------------------------------

    def regions(self) -> list[dict[str, Any]]:
        regions: list[dict[str, Any]] = []
        levels = sorted(self.scene_ids)
        index = levels.index(self.floor.level)
        above = levels[index + 1] if index + 1 < len(levels) else None
        below = levels[index - 1] if index > 0 else None
        for room in sorted(self.floor.rooms, key=lambda r: min(r.cells)):
            kind = TRANSPORT.get(room.type)
            if kind is None:
                continue
            key = (room.type, min(room.cells))  # core rooms share their cells on all floors
            vehicle = next(
                (o for o in self.floor.objects if o.room == room.id and o.kind == kind), None
            )
            up, down = self._halves(room, vehicle)
            label = "Elevator" if room.type == "elevator" else "Stairs"
            regions.append(self._region(key, "arrival", f"{label} (arrive)", self._arrival(room)))
            for target, half, direction in ((above, up, "up"), (below, down, "down")):
                if target is None:
                    continue
                destination = (
                    f"Scene.{self.scene_ids[target]}.Region."
                    f"{stable_id(self.building.seed, self.name, target, 'region', *key, 'arrival')}"
                )
                region = self._region(key, direction, f"{label} {direction}", half)
                region["behaviors"] = [
                    {
                        "_id": self._id("behavior", *key, direction),
                        "name": f"Teleport {direction}",
                        "type": "teleportToken",
                        "system": {"destination": destination, "choice": False},
                        "disabled": False,
                    }
                ]
                regions.append(region)
        return regions

    def _region(
        self, key: tuple[str, Cell], role: str, name: str, box: tuple[float, float, float, float]
    ) -> dict[str, Any]:
        x0, y0 = self.px((box[0], box[1]))
        x1, y1 = self.px((box[2], box[3]))
        return {
            "_id": stable_id(self.building.seed, self.name, self.floor.level, "region", *key, role),
            "name": name,
            "color": "#ffb347" if role != "arrival" else "#00e5ff",
            "shapes": [
                {
                    "type": "rectangle",
                    "x": x0,
                    "y": y0,
                    "width": x1 - x0,
                    "height": y1 - y0,
                    "rotation": 0,
                    "hole": False,
                }
            ],
            "behaviors": [],
            "visibility": 0,
        }

    @staticmethod
    def _halves(
        room: Room, vehicle: PlacedObject | None
    ) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float]]:
        """The stairs (or the room) split along their long side: (up half, down half)."""
        if vehicle is not None:
            x0, y0, x1, y1 = vehicle.x, vehicle.y, vehicle.x + vehicle.w, vehicle.y + vehicle.h
        else:
            xs, ys = [c.x for c in room.cells], [c.y for c in room.cells]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs) + 1, max(ys) + 1
        if x1 - x0 >= y1 - y0:
            mid = (x0 + x1) / 2
            return (x0, y0, mid, y1), (mid, y0, x1, y1)
        mid = (y0 + y1) / 2
        return (x0, y0, x1, mid), (x0, mid, x1, y1)

    def _arrival(self, room: Room) -> tuple[float, float, float, float]:
        """A free spot in front of the room's door (where teleported tokens appear)."""
        clearance = self.floor.door_clearances().get(room.id)
        taken = {c for o in self.floor.objects if o.room == room.id for c in o.cells}
        cells = sorted((clearance or room.cells) - taken) or sorted(room.cells)
        cell = cells[0]
        return (cell.x, cell.y, cell.x + 1, cell.y + 1)


def _macro(name: str) -> str:
    return f"""// Roomplanner: import the floors of "{name}" as scenes (Foundry VTT v12+).
// 1. Copy this folder to <Foundry Data>/{ASSET_ROOT}/{name}/
// 2. Create a script macro with this code and run it once as GM.
const base = "{ASSET_ROOT}/{name}/";
const scenes = await (await fetch(base + "scenes.json")).json();
const existing = scenes.filter(s => game.scenes.has(s._id));
if (existing.length) {{
  ui.notifications.warn(`${{existing.length}} scene(s) of {name} exist; delete them first.`);
}} else {{
  await Scene.createDocuments(scenes, {{keepId: true}});
  ui.notifications.info(`Imported ${{scenes.length}} scene(s) of {name}.`);
}}
"""
