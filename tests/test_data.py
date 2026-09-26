"""Consistency of the bundled data files."""

from importlib import resources

import pytest
import yaml

from roomplanner.layer_rules import load_condition, load_lighting, load_security
from roomplanner.params import BuildingType
from roomplanner.render.theme import load_theme
from roomplanner.rules import load_objects, load_rules


def all_room_types() -> set[str]:
    folder = resources.files("roomplanner") / "data" / "rooms"
    types: set[str] = set()
    for file in folder.iterdir():
        types |= set(yaml.safe_load(file.read_text(encoding="utf-8"))["rooms"])
    return types


@pytest.mark.parametrize("building_type", list(BuildingType))
def test_every_building_type_has_valid_rules(building_type: BuildingType) -> None:
    rules = load_rules(building_type)
    assert rules.program.building is building_type


def test_layer_rules_only_use_known_objects() -> None:
    objects = set(load_objects())
    condition = load_condition()
    assert set(condition.debris) | set(condition.collapse) | set(condition.fixtures) <= objects
    for tier in load_security().tiers.values():
        for rules in tier.furniture.values():
            assert {r.object for r in rules} <= objects


def test_lights_rules_load() -> None:
    assert load_lighting().ceiling.radius > 0


def test_neon_theme_styles_every_room_type_and_object() -> None:
    theme = load_theme("neon")
    assert all_room_types() - set(theme.rooms) == set()
    assert set(load_objects()) - set(theme.objects) == set()
