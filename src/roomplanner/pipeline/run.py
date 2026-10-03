"""Runs the stages with feasibility check and retries. See docs/decisions/0004."""

from __future__ import annotations

from typing import cast

from roomplanner.buildingcode import code_warnings
from roomplanner.errors import InfeasibleError
from roomplanner.model import Building
from roomplanner.params import GenerationParams
from roomplanner.pipeline.base import (
    AllocationError,
    Context,
    FootprintStrategy,
    FurnishingStrategy,
    LayerStrategy,
    LayoutStrategy,
    OpeningsStrategy,
)
from roomplanner.pipeline.registry import resolve
from roomplanner.rules import Rules
from roomplanner.validation import Severity, validate

MAX_ATTEMPTS = 8
MIN_SIDE_CELLS = 4


def run(params: GenerationParams, rules: Rules, seed: int) -> Building:
    width, height = params.width, params.depth
    if min(width, height) < MIN_SIDE_CELLS:
        raise InfeasibleError(
            f"building must be at least {MIN_SIDE_CELLS} cells on each side, got {width} x {height}"
        )

    ctx = Context(params, rules, seed, width, height)
    footprint = cast(FootprintStrategy, resolve("footprint", params.shape.value)).footprint(ctx)
    # A lone room (`params.room`) replaces the building's layout.
    layout_name = "room" if params.room is not None else rules.program.layout
    layout = cast(LayoutStrategy, resolve("layout", layout_name))
    openings = cast(OpeningsStrategy, resolve("openings", "default"))
    furnishing = cast(FurnishingStrategy, resolve("furnishing", rules.program.furnishing))
    layers = [
        cast(LayerStrategy, resolve(stage, name))
        for stage, name in (
            ("lights", rules.program.lighting),
            ("security", rules.program.security),
            ("condition", rules.program.condition),
        )
    ]

    if problems := layout.check_feasibility(ctx, footprint):
        raise InfeasibleError("; ".join(problems))

    best: tuple[tuple[int, int], Building] | None = None
    failure: AllocationError | None = None
    for attempt in range(MAX_ATTEMPTS):
        attempt_ctx = Context(params, rules, seed, width, height, attempt)
        try:
            plan = layout.layout(attempt_ctx, footprint)
        except AllocationError as error:
            failure = error
            continue
        floors = openings.build(attempt_ctx, footprint, plan)
        floors = furnishing.furnish(attempt_ctx, floors)
        for layer in layers:
            floors = layer.apply(attempt_ctx, floors)
        building = Building(params, seed, width, height, tuple(floors), tuple(plan.warnings))
        violations = validate(building, rules)
        hard = sum(v.severity is Severity.HARD for v in violations)
        score = (hard, len(violations))
        if best is None or score < best[0]:
            warnings = (*plan.warnings, *(str(v) for v in violations))
            best = (score, Building(params, seed, width, height, tuple(floors), warnings))
        if hard == 0:
            break

    if best is None:
        raise InfeasibleError(f"required rooms do not fit after {MAX_ATTEMPTS} attempts: {failure}")
    building = best[1]
    code = code_warnings(building, rules)
    return Building(params, seed, width, height, building.floors, (*building.warnings, *code))
