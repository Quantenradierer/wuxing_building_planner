import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from roomplanner.cli import app
from roomplanner.i18n import (
    Language,
    catalogue,
    level_name,
    object_name,
    room_name,
    term,
    web_catalogue,
)
from roomplanner.model import level_name as model_level_name
from roomplanner.params import BuildingType, Condition, EntranceKind, Security, Shape, Wealth
from roomplanner.rules import load_rules
from roomplanner.ui.server import RequestError, foundry_export, options

DATA = Path(__file__).parents[1] / "src" / "roomplanner" / "data"
runner = CliRunner()
PARAMS = {"building_type": "office", "width": 28, "depth": 18, "floors_above": 2, "seed": 5}


def test_english_is_the_ids_and_the_model_names() -> None:
    assert room_name("exam_room") == "exam room"
    assert object_name("filing_cabinet") == "filing cabinet"
    for level in (-2, 0, 3):
        assert level_name(level) == model_level_name(level)


def test_german_names() -> None:
    assert room_name("exam_room", Language.DE) == "Untersuchungsraum"
    assert object_name("desk", Language.DE) == "Schreibtisch"
    assert level_name(0, Language.DE) == "Erdgeschoss"
    assert level_name(2, Language.DE) == "2. Obergeschoss"
    assert level_name(-1, Language.DE) == "Untergeschoss U1"
    assert term("wealth", "luxury", Language.DE) == "Luxus"


def test_unknown_ids_fall_back_to_english() -> None:
    assert room_name("no_such_room", Language.DE) == "no such room"


def test_german_covers_every_room_and_object() -> None:
    german = catalogue(Language.DE)
    rooms = {room for t in BuildingType for room in load_rules(t).rooms}
    assert rooms <= set(german["rooms"]), sorted(rooms - set(german["rooms"]))
    objects = yaml.safe_load((DATA / "objects.yaml").read_text(encoding="utf-8"))["objects"]
    assert set(objects) <= set(german["objects"]), sorted(set(objects) - set(german["objects"]))


def test_german_covers_every_parameter_value_and_ui_text() -> None:
    german = catalogue(Language.DE)
    for group, enum in [
        ("building_type", BuildingType),
        ("wealth", Wealth),
        ("condition", Condition),
        ("security", Security),
        ("shape", Shape),
        ("entrance", EntranceKind),
    ]:
        assert {m.value for m in enum} <= set(german["terms"][group]), group
    assert set(catalogue(Language.EN)["ui"]) == set(german["ui"])


def test_web_catalogue_is_complete_for_both_languages() -> None:
    for language in Language:
        page = web_catalogue(language)
        assert page["terms"]["floor"]["above"]
        assert set(page["ui"]) == set(catalogue(Language.EN)["ui"])


def test_options_carry_the_catalogues() -> None:
    choices = options()
    assert choices["languages"] == ["en", "de"]
    assert choices["i18n"]["de"]["rooms"]["lobby"] == "Lobby"


def test_foundry_names_follow_the_language() -> None:
    _, body = foundry_export({"params": PARAMS, "lang": "de"})
    scene = json.loads(body)["scene"]
    names = [level["name"] for level in scene["levels"]]
    assert "Erdgeschoss" in names
    assert any(r["name"].startswith("Treppe") for r in scene["regions"])
    _, english = foundry_export({"params": PARAMS})
    assert "Ground floor" in [level["name"] for level in json.loads(english)["scene"]["levels"]]


def test_unknown_language_is_a_request_error() -> None:
    with pytest.raises(RequestError, match="lang"):
        foundry_export({"params": PARAMS, "lang": "fr"})


def test_cli_labels_in_german(tmp_path: Path) -> None:
    base = ["generate", "-t", "office", "-w", "28", "-d", "18", "--seed", "5", "-f", "png"]
    english, german = tmp_path / "en.png", tmp_path / "de.png"
    assert runner.invoke(app, [*base, "--labels", "-o", str(english)]).exit_code == 0
    result = runner.invoke(app, [*base, "--labels", "--lang", "de", "-o", str(german)])
    assert result.exit_code == 0, result.output
    assert english.with_name("en_F0.png").read_bytes() != german.with_name("de_F0.png").read_bytes()
    assert runner.invoke(app, [*base, "--lang", "fr"]).exit_code != 0
