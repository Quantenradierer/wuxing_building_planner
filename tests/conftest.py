from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import roomplanner.generator as _generator
from roomplanner.model import Building
from roomplanner.params import BuildingType, GenerationParams

# Test categories, one marker per test file (select with `pytest -m technical` etc.):
#   technical  - data structures, geometry, serialization, exports, CLI, UI: no building logic
#   validation - invariants and quality gates every building must meet (validator, property
#                tests, building code, snapshots)
#   behaviour  - what the layout and furnishing stages produce for a given building type
CATEGORIES: dict[str, str] = {
    "test_bitgrid": "technical",
    "test_chamfer": "technical",
    "test_cli": "technical",
    "test_data": "technical",
    "test_export": "technical",
    "test_geometry": "technical",
    "test_i18n": "technical",
    "test_image": "technical",
    "test_model": "technical",
    "test_pipeline": "technical",
    "test_serialization": "technical",
    "test_ui": "technical",
    "test_ascii": "technical",
    "test_buildingcode": "validation",
    "test_properties": "validation",
    "test_rules": "validation",
    "test_snapshots": "validation",
    "test_validation": "validation",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        item.add_marker(getattr(pytest.mark, CATEGORIES.get(Path(item.path).stem, "behaviour")))


# Generation is deterministic and buildings are immutable, so tests that ask for the same
# parameters share one result. Seedless calls (random seed) and failures are never cached.
_CACHE: OrderedDict[str, Building] = OrderedDict()
_CACHE_SIZE = 256
uncached_generate: Callable[[GenerationParams], Building] = _generator.generate


def _cached_generate(params: GenerationParams) -> Building:
    if params.seed is None:
        return uncached_generate(params)
    key = params.model_dump_json()
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    building = _CACHE[key] = uncached_generate(params)
    if len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return building


_generator.generate = _cached_generate


def make_params(**overrides: Any) -> GenerationParams:
    values: dict[str, Any] = {
        "building_type": BuildingType.OFFICE,
        "width": 40,
        "depth": 24,
        "seed": 7,
    }
    return GenerationParams(**(values | overrides))
