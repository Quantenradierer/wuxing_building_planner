"""Themes for the image renderer: how rooms, walls, openings and objects are drawn.

A theme is data (`data/themes/*.yaml` or any YAML file). Lengths are in cells, colours are
"#rrggbb" or "#rrggbbaa". Room types map to named floor materials; object kinds map to
styles, each naming a shape from `render/shapes.py`. Unknown room types and object kinds
fall back to the defaults, so themes never have to list every type.
"""

from __future__ import annotations

from enum import StrEnum
from functools import cache
from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from roomplanner.errors import RulesError

type Colour = tuple[int, int, int, int]


def colour(value: str) -> Colour:
    """'#rrggbb' or '#rrggbbaa' to an RGBA tuple."""
    digits = value.removeprefix("#")
    if len(digits) == 6:
        digits += "ff"
    if len(digits) != 8:
        raise ValueError(f"invalid colour {value!r}")
    r, g, b, a = (int(digits[i : i + 2], 16) for i in range(0, 8, 2))
    return r, g, b, a


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Pattern(StrEnum):
    PLAIN = "plain"
    TILES = "tiles"  # square tiles, `tile` cells wide
    PLANKS = "planks"  # boards along x, `tile` cells wide
    GRATE = "grate"  # diagonal metal grating


class Material(_Strict):
    floor: str
    pattern: Pattern = Pattern.PLAIN
    tile: int = Field(default=2, gt=0)
    lines: str = Field(default="#00000030", description="Pattern line colour")
    accent: str | None = Field(default=None, description="Neon strip along the walls")


class WallStyle(_Strict):
    colour: str
    edge: str = Field(description="Thin outline of the wall body")
    exterior: float = Field(default=0.4, gt=0, description="Thickness in cells")
    interior: float = Field(default=0.22, gt=0)


class DoorStyle(_Strict):
    leaf: str
    swing: str
    exterior_leaf: str | None = None


class WindowStyle(_Strict):
    frame: str
    glass: str
    glow: str


class ObjectStyle(_Strict):
    shape: str = "box"
    fill: str = "#2a2f3a"
    stroke: str = "#6b7385"
    detail: str = Field(default="#9aa3b5", description="Lines and marks on the object")
    glow: str | None = Field(default=None, description="Neon highlight (screens, LEDs)")


class Theme(_Strict):
    name: str
    background: str
    outside_pattern: Pattern = Pattern.PLAIN
    outside_lines: str = "#ffffff08"
    noise: float = Field(default=0.05, ge=0, le=1, description="Grain strength")
    shadow: str = "#000000a0"
    glow_radius: float = Field(default=0.3, ge=0, description="Blur radius in cells")
    label: str = "#d8dce6"
    grid: str = "#ffffff26"
    walls: WallStyle
    doors: DoorStyle
    windows: WindowStyle
    materials: dict[str, Material]
    default_material: str
    rooms: dict[str, str] = Field(default={}, description="Room type -> material")
    objects: dict[str, ObjectStyle] = {}
    default_object: ObjectStyle = ObjectStyle()

    def material(self, room_type: str) -> Material:
        return self.materials[self.rooms.get(room_type, self.default_material)]

    def object_style(self, kind: str) -> ObjectStyle:
        return self.objects.get(kind, self.default_object)


def load_theme(name_or_path: str) -> Theme:
    """A bundled theme by name, or a theme YAML file by path."""
    path = Path(name_or_path)
    if path.suffix in (".yaml", ".yml"):
        return _load(path.read_text(encoding="utf-8"), str(path))
    return _bundled(name_or_path)


@cache
def _bundled(name: str) -> Theme:
    file = resources.files("roomplanner") / "data" / "themes" / f"{name}.yaml"
    if not file.is_file():
        raise RulesError(f"unknown theme {name!r}")
    return _load(file.read_text(encoding="utf-8"), name)


def _load(text: str, origin: str) -> Theme:
    try:
        theme = Theme.model_validate(yaml.safe_load(text))
    except (yaml.YAMLError, ValidationError) as error:
        raise RulesError(f"theme {origin}: {error}") from error
    missing = {theme.default_material, *theme.rooms.values()} - set(theme.materials)
    if missing:
        raise RulesError(f"theme {origin}: unknown materials {', '.join(sorted(missing))}")
    return theme
