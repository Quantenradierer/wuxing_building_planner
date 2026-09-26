from __future__ import annotations

from enum import StrEnum
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from roomplanner.geometry import Side


class BuildingType(StrEnum):
    OFFICE = "office"
    APARTMENT = "apartment"
    SUPERMARKET = "supermarket"
    CLINIC = "clinic"


class Wealth(StrEnum):
    SQUATTER = "squatter"
    LOW = "low"
    MIDDLE = "middle"
    HIGH = "high"
    LUXURY = "luxury"


class Condition(StrEnum):
    PRISTINE = "pristine"
    MAINTAINED = "maintained"
    RUN_DOWN = "run_down"
    DERELICT = "derelict"
    RUINED = "ruined"


class Security(StrEnum):
    NONE = "none"
    LOW = "low"
    CORPORATE = "corporate"
    AAA = "aaa"


class Shape(StrEnum):
    RECTANGLE = "rectangle"
    L = "l"
    U = "u"
    IRREGULAR = "irregular"


class GenerationParams(BaseModel):
    """Everything that determines a generated building. See docs/requirements.md."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    building_type: BuildingType
    width: int = Field(ge=1, description="East-west extent (bounding box) in cells")
    depth: int = Field(ge=1, description="North-south extent (bounding box) in cells")
    floors_above: int = Field(default=1, ge=1, description="Floors above ground incl. ground")
    floors_below: int = Field(default=0, ge=0, description="Basement levels")
    wealth: Wealth = Wealth.MIDDLE
    condition: Condition = Condition.MAINTAINED
    security: Security = Security.LOW
    shape: Shape = Shape.RECTANGLE
    street_side: Side = Side.S
    # Always set after validation; None only as input meaning "opposite of street_side".
    service_side: Side = Field(default=None, validate_default=False)  # pyright: ignore[reportAssignmentType]
    seed: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _default_service_side(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        values = cast(dict[str, Any], data)
        if values.get("service_side") is None:
            street = Side(values.get("street_side", Side.S))
            return {**values, "service_side": street.opposite}
        return values

    @property
    def levels(self) -> range:
        """All floor levels, lowest basement first."""
        return range(-self.floors_below, self.floors_above)
