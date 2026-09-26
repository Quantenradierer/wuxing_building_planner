"""Invariants that must hold for any parameters: impossible input is rejected, never broken."""

from hypothesis import given, settings
from hypothesis import strategies as st

from roomplanner.errors import InfeasibleError
from roomplanner.generator import generate
from roomplanner.geometry import Side
from roomplanner.model import Building
from roomplanner.params import BuildingType, Condition, GenerationParams, Security, Shape, Wealth
from roomplanner.rules import rules_for
from roomplanner.serialization import from_json, to_json
from roomplanner.validation import hard_violations

IMPLEMENTED = list(BuildingType)

cells = st.integers(min_value=8, max_value=90)

params_strategy = st.builds(
    GenerationParams,
    building_type=st.sampled_from(IMPLEMENTED),
    width=cells,
    depth=cells,
    floors_above=st.integers(min_value=1, max_value=3),
    floors_below=st.integers(min_value=0, max_value=1),
    wealth=st.sampled_from(Wealth),
    condition=st.sampled_from(Condition),
    security=st.sampled_from(Security),
    shape=st.sampled_from([Shape.RECTANGLE, Shape.L]),
    street_side=st.sampled_from(Side),
    service_side=st.sampled_from(Side),
    seed=st.integers(min_value=0, max_value=2**32 - 1),
)


def generate_or_none(params: GenerationParams) -> Building | None:
    try:
        return generate(params)
    except InfeasibleError:
        return None


@settings(max_examples=100, deadline=None)
@given(params_strategy)
def test_generated_buildings_have_no_hard_violations(params: GenerationParams) -> None:
    building = generate_or_none(params)
    if building is not None:
        assert hard_violations(building, rules_for(params.building_type, params.wealth)) == []


@settings(max_examples=25, deadline=None)
@given(params_strategy)
def test_generation_is_deterministic_and_serializable(params: GenerationParams) -> None:
    building = generate_or_none(params)
    if building is not None:
        assert generate(params) == building
        assert from_json(to_json(building)) == building


@settings(max_examples=25, deadline=None)
@given(
    params_strategy.filter(lambda p: min(p.width, p.depth) >= 32 and p.shape is Shape.RECTANGLE),
)
def test_reasonably_sized_rectangular_buildings_are_always_feasible(
    params: GenerationParams,
) -> None:
    assert generate(params) is not None
