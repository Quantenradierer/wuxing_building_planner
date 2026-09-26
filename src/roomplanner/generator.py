"""Building generation entry point."""

from __future__ import annotations

import secrets

from roomplanner.model import Building
from roomplanner.params import GenerationParams
from roomplanner.pipeline import run
from roomplanner.rules import rules_for


def generate(params: GenerationParams) -> Building:
    """Generate a building. Raises InfeasibleError / NotSupportedError for impossible input."""
    seed = params.seed if params.seed is not None else secrets.randbelow(2**32)
    params = params.model_copy(update={"seed": seed})
    rules = rules_for(params.building_type, params.wealth, params.security)
    return run(params, rules, seed)
