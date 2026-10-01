import json
from pathlib import Path

from typer.testing import CliRunner

from roomplanner.cli import app

runner = CliRunner()
BASE = ["generate", "-t", "office", "-w", "28", "-d", "18", "--seed", "5"]


def test_generate_ascii() -> None:
    result = runner.invoke(app, BASE)
    assert result.exit_code == 0, result.output
    assert "== Ground floor ==" in result.output


def test_json_round_trips_through_render(tmp_path: Path) -> None:
    target = tmp_path / "map.json"
    assert runner.invoke(app, [*BASE, "--format", "json", "-o", str(target)]).exit_code == 0
    assert json.loads(target.read_text())["seed"] == 5

    rendered = runner.invoke(app, ["render", str(target)])
    assert rendered.exit_code == 0
    assert rendered.output == runner.invoke(app, BASE).output


def test_sides_are_case_insensitive() -> None:
    assert runner.invoke(app, [*BASE, "--street-side", "n"]).exit_code == 0


def test_errors_exit_non_zero_with_message() -> None:
    result = runner.invoke(app, ["generate", "-t", "office", "-w", "2", "-d", "12"])
    assert result.exit_code == 1
    assert "error: building must be at least" in result.output


def test_all_options(tmp_path: Path) -> None:
    target = tmp_path / "shop.json"
    args = [
        "generate",
        "-t",
        "supermarket",
        "-w",
        "60",
        "-d",
        "48",
        "--floors-above",
        "2",
        "--floors-below",
        "1",
        "--wealth",
        "luxury",
        "--condition",
        "derelict",
        "--security",
        "corporate",
        "--shape",
        "l",
        "--street-side",
        "E",
        "--service-side",
        "N",
        "--seed",
        "3",
        "-f",
        "json",
        "-o",
        str(target),
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    document = json.loads(target.read_text())
    assert document["params"]["wealth"] == "luxury"
    assert document["params"]["condition"] == "derelict"
    assert any(f["objects"] for f in document["floors"])
    rendered = runner.invoke(app, ["render", str(target)])
    assert "objects:" in rendered.output


def test_render_rejects_foreign_json(tmp_path: Path) -> None:
    target = tmp_path / "other.json"
    target.write_text('{"schema_version": 1}')
    result = runner.invoke(app, ["render", str(target)])
    assert result.exit_code == 1
    assert "schema_version" in result.output


def test_generate_images_one_file_per_floor(tmp_path: Path) -> None:
    target = tmp_path / "map.png"
    args = [*BASE, "--floors-above", "2", "-f", "png", "-o", str(target), "--cell-px", "6"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert (tmp_path / "map_F0.png").is_file()
    assert (tmp_path / "map_F1.png").is_file()


def test_render_saved_json_as_webp(tmp_path: Path) -> None:
    source = tmp_path / "map.json"
    assert runner.invoke(app, [*BASE, "-f", "json", "-o", str(source)]).exit_code == 0
    target = tmp_path / "map.webp"
    args = ["render", str(source), "-f", "webp", "-o", str(target), "--cell-px", "6", "--labels"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert (tmp_path / "map_F0.webp").is_file()


def test_unknown_theme_fails(tmp_path: Path) -> None:
    args = [*BASE, "-f", "png", "-o", str(tmp_path / "x.png"), "--theme", "nope"]
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "unknown theme" in result.output


def test_entrances_option(tmp_path: Path) -> None:
    target = tmp_path / "map.json"
    args = [*BASE, "--entrances", "main, emergency", "-f", "json", "-o", str(target)]
    assert runner.invoke(app, args).exit_code == 0
    assert json.loads(target.read_text())["params"]["entrances"] == ["main", "emergency"]
    bad = runner.invoke(app, [*BASE, "--entrances", "garage"])
    assert bad.exit_code == 1
    assert "garage" in bad.output


def test_room_option_generates_only_that_room() -> None:
    result = runner.invoke(app, [*BASE[:4], "12", "-d", "10", "--room", "office"])
    assert result.exit_code == 0, result.output
    assert result.output.count("== ") == 1
    assert "1  office" in result.output
