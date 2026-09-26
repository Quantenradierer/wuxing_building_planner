"""Storerooms stay a small share of the floor area; leftovers join their neighbours."""

import random

from roomplanner.errors import InfeasibleError
from roomplanner.generator import generate
from roomplanner.geometry import Cell, Side
from roomplanner.params import BuildingType, Shape, Wealth
from roomplanner.pipeline.base import PlannedRoom
from roomplanner.pipeline.layout.leftovers import absorb_leftovers
from roomplanner.rules import rules_for

from .conftest import make_params

# Generic storage (not the program rooms a building exists for, like stockrooms).
STORAGE = {"storage", "archive", "records_room", "linen_room", "closet"}
CIRCULATION = {"corridor", "hallway", "stairwell", "elevator", "lobby", "corp_lobby"}
SMALLEST = {BuildingType.HOSPITAL: 48, BuildingType.FACTORY: 40}


def test_generic_storage_is_a_small_share_of_usable_area() -> None:
    """About 5 % of the area outside circulation over all types, no type far above."""
    rng = random.Random(5)
    total, storage = 0, 0
    per_type: dict[BuildingType, float] = {}
    for building_type in BuildingType:
        low = SMALLEST.get(building_type, 36)
        used, stored = 0, 0
        for _ in range(4):
            params = make_params(
                building_type=building_type,
                width=rng.randint(low, 64),
                depth=rng.randint(low, 48),
                floors_above=rng.randint(1, 3),
                floors_below=rng.choice([0, 1]),
                wealth=rng.choice(list(Wealth)),
                shape=rng.choice([Shape.RECTANGLE, Shape.RECTANGLE, Shape.L]),
                street_side=rng.choice(list(Side)),
                seed=rng.randrange(2**32),
            )
            try:
                building = generate(params)
            except InfeasibleError:
                continue
            for floor in building.floors:
                for room in floor.rooms:
                    if room.type not in CIRCULATION:
                        used += room.area
                    if room.type in STORAGE:
                        stored += room.area
        total, storage = total + used, storage + stored
        per_type[building_type] = stored / used if used else 0.0
    assert storage / total <= 0.05, f"{storage / total:.1%}"
    worst = max(per_type, key=lambda t: per_type[t])
    assert per_type[worst] <= 0.12, f"{worst}: {per_type[worst]:.1%}"


def _rect(x0: int, y0: int, x1: int, y1: int) -> frozenset[Cell]:
    return frozenset(Cell(x, y) for x in range(x0, x1) for y in range(y0, y1))


RULES = rules_for(BuildingType.OFFICE, Wealth.MIDDLE)


def test_leftover_storeroom_joins_the_room_beside_it() -> None:
    office = PlannedRoom("office", _rect(0, 0, 6, 10))
    leftover = PlannedRoom("storage", _rect(6, 0, 9, 10), leftover=True)
    rooms = absorb_leftovers([office, leftover], RULES)
    assert [(r.type, r.cells) for r in rooms] == [("office", _rect(0, 0, 9, 10))]


def test_leftover_stays_if_the_neighbour_would_grow_too_big_or_is_core() -> None:
    office = PlannedRoom("office", _rect(0, 0, 8, 10))  # 80 + 40 > 1.4 x 80
    stairwell = PlannedRoom("stairwell", _rect(12, 0, 17, 10))
    leftover = PlannedRoom("storage", _rect(8, 0, 12, 10), leftover=True)
    rooms = absorb_leftovers([office, stairwell, leftover], RULES)
    assert leftover in rooms and len(rooms) == 3


def test_requested_storerooms_are_kept() -> None:
    office = PlannedRoom("office", _rect(0, 0, 6, 10))
    storage = PlannedRoom("storage", _rect(6, 0, 9, 10))
    assert absorb_leftovers([office, storage], RULES) == [office, storage]


def test_thin_leftover_beside_a_corridor_stub_widens_it() -> None:
    stub = PlannedRoom("corridor", _rect(0, 0, 3, 12))
    sliver = PlannedRoom("storage", _rect(3, 0, 8, 12), leftover=True)
    rooms = absorb_leftovers([stub, sliver], RULES)
    assert [(r.type, r.cells) for r in rooms] == [("corridor", _rect(0, 0, 8, 12))]


def test_small_apartment_segments_become_studios_not_storerooms() -> None:
    """A strip too narrow for a flat gets a studio (an optional fill room for leftovers)."""
    building = generate(
        make_params(building_type=BuildingType.APARTMENT, width=40, depth=24, floors_above=2)
    )
    types = [r.type for f in building.floors for r in f.rooms]
    assert types.count("storage") <= 1
