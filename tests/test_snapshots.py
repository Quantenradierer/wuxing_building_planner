"""ASCII snapshots of fixed parameter sets. Rewrite with UPDATE_SNAPSHOTS=1 after reviewing."""

import os
from pathlib import Path
from typing import Any

import pytest

from roomplanner.generator import generate
from roomplanner.geometry import Side
from roomplanner.render.ascii import render_building

from .conftest import make_params

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

CASES: dict[str, dict[str, Any]] = {
    "office_small_one_floor": {"width_m": 10, "depth_m": 6, "seed": 1},
    "office_multi_level": {"width_m": 8, "depth_m": 5, "floors_above": 2, "floors_below": 1},
    "office_street_west_service_north": {
        "width_m": 9,
        "depth_m": 7,
        "street_side": Side.W,
        "service_side": Side.N,
    },
}


@pytest.mark.parametrize("name", CASES)
def test_ascii_snapshot(name: str) -> None:
    actual = render_building(generate(make_params(**CASES[name])))
    path = SNAPSHOT_DIR / f"{name}.txt"
    if os.environ.get("UPDATE_SNAPSHOTS") or not path.exists():
        path.write_text(actual, encoding="utf-8")
        if not os.environ.get("UPDATE_SNAPSHOTS"):
            pytest.fail(f"snapshot {path.name} created, review and re-run")
    assert actual == path.read_text(encoding="utf-8")
