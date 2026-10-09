import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

from roomplanner.cli import app
from roomplanner.errors import RoomplannerError
from roomplanner.export.common import ExportOptions
from roomplanner.export.foundry import to_foundry
from roomplanner.export.uvtt import to_uvtt
from roomplanner.generator import generate
from roomplanner.model import Building, OpeningKind, OpeningState
from roomplanner.params import Condition, Security
from roomplanner.render.theme import load_theme

from .conftest import make_params

SMALL = ExportOptions(grid_m=1.0, cell_px=6)


def building(**overrides: object) -> Building:
    return generate(make_params(width=40, depth=26, floors_above=3, **overrides))


def test_uvtt_document() -> None:
    b = building(condition=Condition.DERELICT)
    ground = b.floor(0)
    doc = to_uvtt(b, ground, load_theme("neon"), SMALL)
    assert doc["format"] == 0.3
    assert doc["resolution"]["pixels_per_grid"] == 12
    # 40 x 26 cells plus one 1 m square of padding on each side = 22 x 15 squares.
    assert doc["resolution"]["map_size"] == {"x": 22.0, "y": 15.0}
    image = Image.open(io.BytesIO(base64.b64decode(doc["image"])))
    assert image.size == (22 * 12, 15 * 12)
    portals = [
        o
        for o in ground.openings
        if o.kind in (OpeningKind.DOOR, OpeningKind.WINDOW)
        and o.state in (OpeningState.INTACT, OpeningState.BROKEN)
    ]
    assert len(doc["portals"]) == len(portals)
    assert doc["line_of_sight"]
    for wall in doc["line_of_sight"]:
        (a, b_) = wall
        assert a["x"] == b_["x"] or a["y"] == b_["y"]  # straight runs only
    assert all(p["closed"] in (True, False) for p in doc["portals"])


def test_uvtt_without_lights() -> None:
    b = building()
    options = ExportOptions(grid_m=1.0, cell_px=6, lights=False)
    assert to_uvtt(b, b.floor(0), load_theme("neon"), options)["lights"] == []


def test_grid_size_must_fit_the_cells() -> None:
    assert ExportOptions(grid_m=1.5, cell_px=10).pixels_per_square == 30
    with pytest.raises(RoomplannerError, match=r"multiple of 0\.5"):
        _ = ExportOptions(grid_m=0.7).cells_per_square


def test_foundry_scene_has_a_level_per_floor_and_stairs_between_them() -> None:
    b = building(security=Security.AAA)
    scene = to_foundry(b, load_theme("neon"), SMALL, "tower").scene
    levels = {level["_id"]: level for level in scene["levels"]}
    assert len(levels) == 3
    assert scene["initialLevel"] == "defaultLevel0000"
    assert [level["elevation"] for level in scene["levels"]] == [
        {"bottom": 0.0, "top": 3.0},
        {"bottom": 3.0, "top": 6.0},
        {"bottom": 6.0, "top": 9.0},
    ]
    assert scene["grid"] == {"type": 1, "size": 12, "distance": 1.0, "units": "m"}
    for placeable in scene["walls"] + scene["lights"]:
        assert len(placeable["levels"]) == 1
        assert placeable["levels"][0] in levels
    shafts = scene["regions"]
    assert shafts
    for region in shafts:
        assert len(region["levels"]) >= 2
        assert set(region["levels"]) <= set(levels)
        assert [b["type"] for b in region["behaviors"]] == ["changeLevel"]
        top_level = levels[region["levels"][-1]]
        assert region["elevation"]["top"] > top_level["elevation"]["bottom"]
    doors = [w for w in scene["walls"] if w["door"] == 1 and w["sight"] == 20]
    assert any(w["ds"] == 2 for w in doors)  # locked
    sliding = sum(o.sliding for f in b.floors for o in f.openings)
    assert sliding
    assert sum(w.get("animation", {}).get("type") == "slide" for w in doors) == sliding
    windows = [w for w in scene["walls"] if w["sight"] == 0]
    assert windows and all(w["move"] == 20 and w["door"] == 1 for w in windows)
    ids = [p["_id"] for p in scene["walls"] + scene["lights"] + scene["regions"]]
    assert len(ids) == len(set(ids))


def test_foundry_export_is_one_deterministic_file_with_its_images() -> None:
    b = building()
    first = to_foundry(b, load_theme("neon"), SMALL, "tower")
    assert first.json() == to_foundry(b, load_theme("neon"), SMALL, "tower").json()
    document = json.loads(first.json())
    assert (document["format"], document["version"], document["name"]) == (
        "schattenakte",
        1,
        "tower",
    )
    scene = document["scene"]
    files = [level["background"]["src"] for level in scene["levels"]]
    assert files == ["tower_F0.webp", "tower_F1.webp", "tower_F2.webp", "tower_F3.webp"]
    assert set(document["images"]) == set(files)
    image = Image.open(io.BytesIO(base64.b64decode(document["images"]["tower_F0.webp"])))
    assert (image.format, image.size) == ("WEBP", (scene["width"], scene["height"]))


def test_cli_exports(tmp_path: Path) -> None:
    runner = CliRunner()
    base = ["generate", "-t", "office", "-w", "28", "-d", "18", "--seed", "5", "--cell-px", "6"]
    target = str(tmp_path / "out" / "m.dd2vtt")
    uvtt = runner.invoke(app, [*base, "-f", "dd2vtt", "-o", target, "--baked-lighting"])
    assert uvtt.exit_code == 0, uvtt.output
    assert (tmp_path / "out" / "m_F0.dd2vtt").is_file()
    target = tmp_path / "vtt" / "lab.schattenakte.json"
    foundry = runner.invoke(app, [*base, "-f", "foundry", "-o", str(target)])
    assert foundry.exit_code == 0, foundry.output
    assert json.loads(target.read_text())["name"] == "lab"
    bad = runner.invoke(app, [*base, "-f", "dd2vtt", "--grid-m", "0.7"])
    assert bad.exit_code == 1
    assert "multiple of 0.5" in bad.output


def test_lighting_is_left_to_the_vtt_unless_baked() -> None:
    b = building()
    theme = load_theme("neon")
    flat = to_uvtt(b, b.floor(0), theme, SMALL)
    baked_options = ExportOptions(grid_m=1.0, cell_px=6, baked_lighting=True)
    baked = to_uvtt(b, b.floor(0), theme, baked_options)
    assert flat["environment"]["baked_lighting"] is False
    assert baked["environment"]["baked_lighting"] is True
    assert flat["image"] != baked["image"]
    assert to_foundry(b, theme, SMALL, "t").scene["environment"]["darknessLevel"] > 0
    assert to_foundry(b, theme, baked_options, "t").scene["environment"]["darknessLevel"] == 0
