"""The rooms, furniture and security seeds vary their part of the building independently."""

from __future__ import annotations

from typing import Any, cast

from roomplanner.generator import generate
from roomplanner.params import Security
from roomplanner.serialization import to_dict

from .conftest import make_params


def _build(**seeds: int) -> tuple[str, str]:
    params = make_params(width=48, depth=30, floors_above=2, security=Security.AAA, seed=1, **seeds)
    floors = cast(list[dict[str, Any]], to_dict(generate(params))["floors"])
    return str([f["rooms"] for f in floors]), str(floors)


def test_furniture_and_security_seeds_leave_the_layout_alone() -> None:
    base_rooms, base_all = _build()
    for seeds in ({"furniture_seed": 9}, {"security_seed": 9}):
        rooms, everything = _build(**seeds)
        assert rooms == base_rooms
        assert everything != base_all


def test_rooms_seed_changes_the_room_choice() -> None:
    assert _build(rooms_seed=9)[0] != _build()[0]
