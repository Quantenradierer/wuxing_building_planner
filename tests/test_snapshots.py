"""ASCII snapshots of fixed parameter sets. Rewrite with UPDATE_SNAPSHOTS=1 after reviewing."""

import os
from pathlib import Path
from typing import Any

import pytest

from roomplanner.generator import generate
from roomplanner.geometry import Side
from roomplanner.params import BuildingType, Condition, Security, Shape, Wealth
from roomplanner.render.ascii import render_building

from .conftest import make_params

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

CASES: dict[str, dict[str, Any]] = {
    "office_small_one_floor": {"width": 32, "depth": 18, "seed": 1},
    "office_multi_level": {"width": 48, "depth": 28, "floors_above": 2, "floors_below": 1},
    "office_street_west_service_north": {
        "width": 60,
        "depth": 44,
        "street_side": Side.W,
        "service_side": Side.N,
    },
    "office_l_shape": {"width": 64, "depth": 52, "shape": Shape.L, "floors_above": 2, "seed": 2},
    "office_u_shape": {"width": 64, "depth": 48, "shape": Shape.U, "seed": 3},
    "hotel": {"building_type": BuildingType.HOTEL, "width": 56, "depth": 36, "seed": 2},
    "nightclub_ruined_aaa": {
        "building_type": BuildingType.NIGHTCLUB,
        "width": 56,
        "depth": 36,
        "seed": 2,
        "condition": Condition.RUINED,
        "security": Security.AAA,
    },
    "clinic_t_shape": {
        "building_type": BuildingType.CLINIC,
        "width": 72,
        "depth": 60,
        "shape": Shape.T,
        "seed": 5,
    },
    "apartment_block": {
        "building_type": BuildingType.APARTMENT,
        "width": 54,
        "depth": 34,
        "floors_above": 2,
        "seed": 4,
    },
    "clinic_with_surgery": {
        "building_type": BuildingType.CLINIC,
        "width": 60,
        "depth": 32,
        "floors_above": 3,
        "floors_below": 1,
        "seed": 5,
    },
    "supermarket_two_floors": {
        "building_type": BuildingType.SUPERMARKET,
        "width": 64,
        "depth": 40,
        "floors_above": 2,
        "floors_below": 1,
        "seed": 6,
    },
    "stuffer_shack": {"building_type": BuildingType.SUPERMARKET, "width": 30, "depth": 26},
    "data_centre": {
        "building_type": BuildingType.DATA_CENTRE,
        "width": 60,
        "depth": 44,
        "floors_above": 2,
        "floors_below": 1,
        "security": Security.CORPORATE,
        "seed": 3,
    },
    "gun_shop_with_flat": {
        "building_type": BuildingType.GUN_SHOP,
        "width": 26,
        "depth": 18,
        "floors_above": 2,
        "floors_below": 1,
        "wealth": Wealth.HIGH,
        "seed": 3,
    },
    "ramen_bar": {"building_type": BuildingType.RAMEN_BAR, "width": 24, "depth": 18, "seed": 4},
    "office_luxury_executive": {
        "width": 72,
        "depth": 36,
        "floors_above": 3,
        "wealth": Wealth.LUXURY,
        "seed": 3,
    },
    "casino": {
        "building_type": BuildingType.CASINO,
        "width": 52,
        "depth": 36,
        "floors_above": 3,
        "seed": 7,
        "security": Security.CORPORATE,
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
