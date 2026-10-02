"""Consistency of the bundled data files."""

from importlib import resources

import pytest
import yaml

from roomplanner.layer_rules import load_condition, load_lighting, load_security
from roomplanner.params import BuildingType, Condition, Wealth
from roomplanner.render.theme import load_theme
from roomplanner.rules import load_groups, load_objects, load_rules


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
    placeable = objects | set(load_groups())
    assert (
        set(condition.debris)
        | set(condition.rubble)
        | set(condition.collapse)
        | set(condition.fixtures)
        <= objects
    )
    for tier in load_security().tiers.values():
        for rules in tier.furniture.values():
            assert {r.object for r in rules} <= placeable


def test_lights_rules_load() -> None:
    assert load_lighting().ceiling.radius > 0


def test_neon_theme_styles_every_room_type_and_object() -> None:
    theme = load_theme("neon")
    assert all_room_types() - set(theme.rooms) == set()
    assert set(load_objects()) - set(theme.objects) == set()


def test_object_glyphs_avoid_wall_door_and_room_number_glyphs() -> None:
    from roomplanner.render.ascii import RESERVED_GLYPHS

    clashes = {k: s.glyph for k, s in load_objects().items() if s.glyph in RESERVED_GLYPHS}
    assert not clashes


@pytest.mark.parametrize(
    ("condition", "looks"),
    [
        (Condition.PRISTINE, ["middle", "middle", "high", "luxury", "luxury"]),
        (Condition.MAINTAINED, ["low", "low", "middle", "high", "high"]),
        (Condition.RUN_DOWN, ["squatter", "squatter", "low", "middle", "middle"]),
        (Condition.DERELICT, ["squatter", "squatter", "squatter", "low", "low"]),
        (Condition.RUINED, ["squatter", "squatter", "squatter", "low", "low"]),
    ],
)
def test_condition_moves_the_sprite_look(condition: Condition, looks: list[str]) -> None:
    tier = load_condition().tiers[condition]
    assert [tier.look(w) for w in Wealth] == looks


def test_only_derelict_and_ruined_leave_rubble() -> None:
    tiers = load_condition().tiers
    assert [c for c in Condition if tiers[c].rubble] == [Condition.DERELICT, Condition.RUINED]
