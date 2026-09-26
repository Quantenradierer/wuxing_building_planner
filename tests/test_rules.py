import pytest
from pydantic import ValidationError

from roomplanner.params import BuildingType, Wealth
from roomplanner.rules import (
    RoomEntry,
    apply_wealth,
    evaluate,
    load_rules,
    rules_for,
)

from .conftest import make_params


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("floors_total >= 3", True),
        ("floors_total >= 4", False),
        ("floors_above - floors_below == 1", True),
        ("level == 0 and not floors_below", False),
        ("1 < floors_above <= 2 or level < 0", True),
    ],
)
def test_when_expressions(expression: str, expected: bool) -> None:
    values = {"floors_above": 2, "floors_below": 1, "floors_total": 3, "level": 0}
    assert evaluate(expression, values) is expected


@pytest.mark.parametrize("expression", ["__import__('os')", "floors ** 2", "wealth > 1", "x()"])
def test_when_expressions_reject_anything_else(expression: str) -> None:
    with pytest.raises(ValidationError):
        RoomEntry(room="toilet", count=1, when=expression)


def test_entry_needs_exactly_one_quantity() -> None:
    with pytest.raises(ValidationError, match="exactly one of"):
        RoomEntry(room="toilet", count=1, fill=True)


def test_office_rules_load_and_reference_known_rooms() -> None:
    rules = load_rules(BuildingType.OFFICE)
    assert {"toilet", "corridor", "office"} <= set(rules.rooms)


def test_wealth_scales_areas_and_corridors() -> None:
    base = load_rules(BuildingType.OFFICE)
    squatter = apply_wealth(base, Wealth.SQUATTER)
    luxury = apply_wealth(base, Wealth.LUXURY)
    assert (
        squatter.spec("office").area[1]
        < base.spec("office").area[1]
        < luxury.spec("office").area[1]
    )
    assert squatter.program.corridor.width < luxury.program.corridor.width
    assert squatter.spec("office").min_side == base.spec("office").min_side


def test_wealth_filters_roles_and_entries() -> None:
    squatter = rules_for(BuildingType.OFFICE, Wealth.SQUATTER)
    luxury = rules_for(BuildingType.OFFICE, Wealth.LUXURY)
    assert "executive" not in squatter.program.floor_roles
    assert "executive" in luxury.program.floor_roles
    kitchens = [
        e for e in squatter.program.floor_roles["standard"].rooms if e.room == "kitchenette"
    ]
    assert kitchens == []


def test_executive_floor_on_top_of_tall_luxury_offices() -> None:
    params = make_params(wealth=Wealth.LUXURY, floors_above=3)
    rules = rules_for(params.building_type, params.wealth)
    assert rules.role_for(2, params)[0] == "executive"
    assert rules.role_for(1, params)[0] == "standard"
