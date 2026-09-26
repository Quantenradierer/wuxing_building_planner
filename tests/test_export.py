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
    passable_doors = [
        o
        for o in ground.openings
        if o.kind is OpeningKind.DOOR and o.state in (OpeningState.INTACT, OpeningState.BROKEN)
    ]
    assert len(doc["portals"]) == len(passable_doors)
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


def test_foundry_scenes_link_their_stairs() -> None:
    b = building(security=Security.AAA)
    export = to_foundry(b, load_theme("neon"), SMALL, "tower")
    assert len(export.scenes) == 3
    regions = {
        f"Scene.{scene['_id']}.Region.{region['_id']}"
        for scene in export.scenes
        for region in scene["regions"]
    }
    destinations = [
        behavior["system"]["destination"]
        for scene in export.scenes
        for region in scene["regions"]
        for behavior in region["behaviors"]
    ]
    assert destinations
    assert set(destinations) <= regions
    ground = export.scenes[0]
    assert ground["grid"] == {"type": 1, "size": 12, "distance": 1.0, "units": "m"}
    doors = [w for w in ground["walls"] if w["door"] == 1]
    assert any(w["ds"] == 2 for w in doors)  # locked
    windows = [w for w in ground["walls"] if w["sight"] == 0]
    assert windows and all(w["move"] == 20 for w in windows)
    ids = [w["_id"] for s in export.scenes for w in s["walls"]]
    assert len(ids) == len(set(ids))


def test_foundry_export_is_deterministic_and_writes_files(tmp_path: Path) -> None:
    b = building()
    first = to_foundry(b, load_theme("neon"), SMALL, "tower")
    second = to_foundry(b, load_theme("neon"), SMALL, "tower")
    assert first.scenes == second.scenes
    paths = first.write(tmp_path / "tower")
    assert (tmp_path / "tower" / "scenes.json").is_file()
    assert "keepId: true" in (tmp_path / "tower" / "import-macro.js").read_text()
    assert len([p for p in paths if p.suffix == ".png"]) == 3
    scenes = json.loads((tmp_path / "tower" / "scenes.json").read_text())
    assert scenes[0]["background"]["src"] == "roomplanner/tower/tower_F0.png"


def test_cli_exports(tmp_path: Path) -> None:
    runner = CliRunner()
    base = ["generate", "-t", "office", "-w", "28", "-d", "18", "--seed", "5", "--cell-px", "6"]
    target = str(tmp_path / "out" / "m.dd2vtt")
    uvtt = runner.invoke(app, [*base, "-f", "dd2vtt", "-o", target, "--baked-lighting"])
    assert uvtt.exit_code == 0, uvtt.output
    assert (tmp_path / "out" / "m_F0.dd2vtt").is_file()
    foundry = runner.invoke(app, [*base, "-f", "foundry", "-o", str(tmp_path / "scene")])
    assert foundry.exit_code == 0, foundry.output
    assert (tmp_path / "scene" / "scene_F0.png").is_file()
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
    assert to_foundry(b, theme, SMALL, "t").scenes[0]["environment"]["darknessLevel"] > 0
    scenes = to_foundry(b, theme, baked_options, "t").scenes
    assert scenes[0]["environment"]["darknessLevel"] == 0
