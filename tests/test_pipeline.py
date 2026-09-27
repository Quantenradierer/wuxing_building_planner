"""Replaceable strategies and the failure policy (docs/decisions/0003, 0004)."""

from dataclasses import replace

import pytest

from roomplanner.errors import InfeasibleError, RulesError
from roomplanner.geometry import Cell
from roomplanner.pipeline import run
from roomplanner.pipeline.base import AllocationError, BuildingPlan, Context
from roomplanner.pipeline.layout.corridor import CorridorLayout
from roomplanner.pipeline.registry import resolve
from roomplanner.rules import Rules, rules_for

from .conftest import make_params

CALLS: list[int] = []


class CountingLayout(CorridorLayout):
    """A custom layout loaded by import path: the corridor layout, counting attempts."""

    def layout(self, ctx: Context, footprint: frozenset[Cell]) -> BuildingPlan:
        CALLS.append(ctx.attempt)
        return super().layout(ctx, footprint)


class NeverFitsLayout(CorridorLayout):
    def layout(self, ctx: Context, footprint: frozenset[Cell]) -> BuildingPlan:
        raise AllocationError("never fits")


class BrokenLayout(CorridorLayout):
    """Always loses a room: every attempt has a hard violation."""

    def layout(self, ctx: Context, footprint: frozenset[Cell]) -> BuildingPlan:
        plan = super().layout(ctx, footprint)
        for floor in plan.floors:
            victim = next(r for r in floor.rooms if r.type not in ("corridor", "stairwell"))
            floor.rooms.remove(victim)
        return plan


def with_layout(name: str) -> Rules:
    params = make_params()
    rules = rules_for(params.building_type, params.wealth)
    return replace(rules, program=rules.program.model_copy(update={"layout": name}))


def test_strategies_resolve_by_name_and_import_path() -> None:
    assert isinstance(resolve("layout", "corridor"), CorridorLayout)
    assert isinstance(resolve("layout", "tests.test_pipeline:CountingLayout"), CountingLayout)


@pytest.mark.parametrize("name", ["nope", "tests.test_pipeline:Nope", "no.such.module:X"])
def test_unknown_strategies_are_rules_errors(name: str) -> None:
    with pytest.raises(RulesError):
        resolve("layout", name)


def test_custom_layout_from_rules_runs_through_the_pipeline() -> None:
    CALLS.clear()
    building = run(make_params(), with_layout("tests.test_pipeline:CountingLayout"), 7)
    assert CALLS == [0]
    assert building.floors


def test_allocation_failures_are_retried_then_infeasible() -> None:
    with pytest.raises(InfeasibleError, match="after 8 attempts: never fits"):
        run(make_params(), with_layout("tests.test_pipeline:NeverFitsLayout"), 7)


def test_hard_violations_after_all_attempts_give_best_effort_with_warnings() -> None:
    building = run(make_params(), with_layout("tests.test_pipeline:BrokenLayout"), 7)
    assert any(w.startswith("[hard]") and "belong to no room" in w for w in building.warnings)
