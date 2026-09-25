from hypothesis import given, settings
from hypothesis import strategies as st

from roomplanner.generator import generate
from roomplanner.geometry import Side
from roomplanner.params import BuildingType, Condition, GenerationParams, Security, Wealth
from roomplanner.serialization import from_json, to_json
from roomplanner.validation import hard_violations

params_strategy = st.builds(
    GenerationParams,
    building_type=st.sampled_from(BuildingType),
    width_m=st.floats(min_value=2, max_value=60).map(lambda v: round(v * 2) / 2),
    depth_m=st.floats(min_value=2, max_value=60).map(lambda v: round(v * 2) / 2),
    floors_above=st.integers(min_value=1, max_value=4),
    floors_below=st.integers(min_value=0, max_value=2),
    wealth=st.sampled_from(Wealth),
    condition=st.sampled_from(Condition),
    security=st.sampled_from(Security),
    street_side=st.sampled_from(Side),
    service_side=st.sampled_from(Side),
    seed=st.integers(min_value=0, max_value=2**32 - 1),
)


@settings(max_examples=150, deadline=None)
@given(params_strategy)
def test_generated_buildings_have_no_hard_violations(params: GenerationParams) -> None:
    assert hard_violations(generate(params)) == []


@settings(max_examples=50, deadline=None)
@given(params_strategy)
def test_generation_is_deterministic_and_serializable(params: GenerationParams) -> None:
    building = generate(params)
    assert generate(params) == building
    assert from_json(to_json(building)) == building
