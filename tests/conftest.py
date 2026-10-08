from __future__ import annotations

from typing import Any

from roomplanner.params import BuildingType, GenerationParams


def make_params(**overrides: Any) -> GenerationParams:
    values: dict[str, Any] = {
        "building_type": BuildingType.OFFICE,
        "width": 40,
        "depth": 24,
        "seed": 7,
    }
    return GenerationParams(**(values | overrides))


def _cache_generation() -> None:
    """Share buildings between tests: the same seeded params give the same (immutable) building.

    Installed before the test modules do `from roomplanner.generator import generate`. Tests that
    check determinism call `generate_uncached`.
    """
    import roomplanner.generator as generator
    from roomplanner.model import Building

    original = generator.generate
    cache: dict[str, Building] = {}

    def cached(params: GenerationParams) -> Building:
        if params.seed is None:
            return original(params)
        key = params.model_dump_json()
        if key not in cache:
            cache[key] = original(params)
        return cache[key]

    cached.uncached = original  # type: ignore[attr-defined]
    generator.generate = cached


_cache_generation()


def generate_uncached(params: GenerationParams) -> Any:
    import roomplanner.generator as generator

    return generator.generate.uncached(params)  # type: ignore[attr-defined]
