import json
from pathlib import Path

from typer.testing import CliRunner

from roomplanner.cli import app

runner = CliRunner()
BASE = ["generate", "-t", "office", "-w", "10", "-d", "6", "--seed", "5"]


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
    result = runner.invoke(app, ["generate", "-t", "office", "-w", "1", "-d", "6"])
    assert result.exit_code == 1
    assert "error: building must be at least" in result.output
