"""Rules for the layers added after furnishing: lights, security and condition.

They are shared by all building types: `data/lights.yaml`, `data/security.yaml` and
`data/condition.yaml`. Lengths are in cells, areas in cells.
"""

from __future__ import annotations

from functools import cache
from importlib import resources

from pydantic import BaseModel, ConfigDict, Field

from roomplanner.params import Condition, Security
from roomplanner.rules import FurnitureRule, load_yaml


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- lights ---------------------------------------------------------------------------


class LightRule(_Strict):
    kind: str = "ceiling"
    spacing: int = Field(default=8, gt=0, description="Ceiling grid spacing in cells")
    radius: float = Field(default=8, gt=0)
    colour: str = "#fff1dc"
    intensity: float = Field(default=0.7, ge=0, le=1)


class LightOverride(_Strict):
    """Per room type changes of the ceiling light; `none: true` means no ceiling light."""

    none: bool = False
    spacing: int | None = Field(default=None, gt=0)
    radius: float | None = Field(default=None, gt=0)
    colour: str | None = None
    intensity: float | None = Field(default=None, ge=0, le=1)


class Lighting(_Strict):
    ceiling: LightRule
    rooms: dict[str, LightOverride] = {}
    accents: dict[str, list[LightRule]] = Field(
        default={}, description="Extra lights per room type, against a wall"
    )
    entrances: dict[str, LightRule] = Field(
        default={}, description="Light outside exterior doors, by entrance kind"
    )

    def ceiling_for(self, room_type: str) -> LightRule | None:
        override = self.rooms.get(room_type)
        if override is None:
            return self.ceiling
        if override.none:
            return None
        changes = override.model_dump(exclude_none=True, exclude={"none"})
        return self.ceiling.model_copy(update=changes)


# --- security -------------------------------------------------------------------------


class LockRule(_Strict):
    lock: str = Field(description="none, mechanical, maglock, cardreader, biometric")
    material: str = "standard"
    rating: int | None = Field(default=None, gt=0, description="Default: the tier's rating")


class CameraRule(_Strict):
    rooms: list[str] = Field(default=[], description="Room types with a camera")
    corridor_per: int | None = Field(
        default=None, gt=0, description="One corridor camera per this many corridor cells"
    )
    entrances: bool = Field(default=False, description="A camera inside every exterior door")


class SecurityTier(_Strict):
    rating: int = Field(gt=0)
    exterior: LockRule
    interior: LockRule | None = Field(default=None, description="Default for interior doors")
    rooms: dict[str, LockRule] = Field(default={}, description="Locks by the room entered")
    cameras: CameraRule = CameraRule()
    alarm_panels: bool = Field(default=False, description="Beside every exterior door")
    motion_sensors: list[str] = Field(default=[], description="Room types with a sensor")
    floodlights: LightRule | None = Field(default=None, description="Outside exterior doors")
    furniture: dict[str, list[FurnitureRule]] = Field(
        default={}, description="Extra objects per room type (guard desks, …)"
    )


class SecurityTable(_Strict):
    tiers: dict[Security, SecurityTier]


# --- condition ------------------------------------------------------------------------


class ConditionTier(_Strict):
    furniture_removed: float = Field(default=0, ge=0, le=1)
    debris_per: int | None = Field(default=None, gt=0, description="Debris per N cells of room")
    doors_broken: float = Field(default=0, ge=0, le=1)
    doors_missing: float = Field(default=0, ge=0, le=1)
    doors_blocked: float = Field(default=0, ge=0, le=1)
    windows_broken: float = Field(default=0, ge=0, le=1)
    breaches: float = Field(default=0, ge=0, description="Wall breaches per 1000 floor cells")
    collapses: float = Field(default=0, ge=0, description="Collapsed spots per 1000 cells")
    lights_flicker: float = Field(default=0, ge=0, le=1)
    lights_off: float = Field(default=0, ge=0, le=1)
    power: bool = Field(default=True, description="False: only emergency lights work")


class ConditionTable(_Strict):
    fixtures: list[str] = Field(default=[], description="Objects never removed")
    debris: list[str] = Field(description="Object kinds used as debris")
    collapse: list[str] = Field(description="Object kinds used for collapsed spots")
    tiers: dict[Condition, ConditionTier]


def _data(name: str) -> str:
    return (resources.files("roomplanner") / "data" / name).read_text(encoding="utf-8")


@cache
def load_lighting() -> Lighting:
    return load_yaml(Lighting, _data("lights.yaml"), "lights.yaml")


@cache
def load_security() -> SecurityTable:
    return load_yaml(SecurityTable, _data("security.yaml"), "security.yaml")


@cache
def load_condition() -> ConditionTable:
    return load_yaml(ConditionTable, _data("condition.yaml"), "condition.yaml")
