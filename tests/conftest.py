from __future__ import annotations

from typing import Any

from roomplanner.params import BuildingType, GenerationParams


def make_params(**overrides: Any) -> GenerationParams:
    values: dict[str, Any] = {
        "building_type": BuildingType.OFFICE,
        "width_m": 20,
        "depth_m": 12,
        "seed": 7,
    }
    return GenerationParams(**(values | overrides))
