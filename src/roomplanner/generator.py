"""Building generation entry point."""

from __future__ import annotations

import secrets

from roomplanner.model import Building
from roomplanner.params import GenerationParams
from roomplanner.pipeline import run
from roomplanner.rules import load_rules


def generate(params: GenerationParams) -> Building:
    """Generate a building. Raises InfeasibleError / NotSupportedError for impossible input."""
    seed = params.seed if params.seed is not None else secrets.randbelow(2**32)
    params = params.model_copy(update={"seed": seed})
    return run(params, load_rules(params.building_type), seed)
