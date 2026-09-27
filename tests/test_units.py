from collections import defaultdict

import pytest

from roomplanner.generator import generate
from roomplanner.geometry import Side
from roomplanner.model import Floor, OpeningKind, Room
from roomplanner.params import BuildingType, Wealth
from roomplanner.validation import hard_violations

from .conftest import make_params


def door_pairs(floor: Floor) -> list[tuple[Room, Room]]:
    pairs: list[tuple[Room, Room]] = []
    for opening in floor.openings:
        if opening.kind is not OpeningKind.DOOR:
            continue
        a, b = (floor.room_at(c) for c in opening.edges[0].cells())
        if a is not None and b is not None:
            pairs.append((a, b))
    return pairs


def apartments() -> list[Floor]:
    params = make_params(building_type=BuildingType.APARTMENT, width=60, depth=34, floors_above=2)
    return list(generate(params).floors)


def test_every_unit_has_exactly_one_door_to_the_outside() -> None:
    for floor in apartments():
        outside: dict[str, int] = defaultdict(int)
        for a, b in door_pairs(floor):
            if a.unit != b.unit:
                for room in (a, b):
                    if room.unit is not None:
                        outside[room.unit] += 1
        units = {r.unit for r in floor.rooms if r.unit is not None}
        assert units, "expected apartments"
        assert all(outside[u] == 1 for u in units), outside


def test_unit_doors_open_into_the_hallway() -> None:
    for floor in apartments():
        for a, b in door_pairs(floor):
            if a.unit != b.unit:
                inside = a if a.unit is not None else b
                assert inside.type == "hallway"


def test_bathrooms_and_bedrooms_are_not_passed_through() -> None:
    for floor in apartments():
        doors_per_room: dict[str, int] = defaultdict(int)
        for a, b in door_pairs(floor):
            doors_per_room[a.id] += 1
            doors_per_room[b.id] += 1
        for room in floor.rooms:
            if room.type in ("bathroom", "bedroom"):
                assert doors_per_room[room.id] == 1, room


def test_bedrooms_open_to_living_room_or_hallway() -> None:
    for floor in apartments():
        for a, b in door_pairs(floor):
            for room, other in ((a, b), (b, a)):
                if room.type == "bedroom":
                    assert other.type in ("living_room", "hallway")


def test_clinic_surgery_floor() -> None:
    params = make_params(building_type=BuildingType.CLINIC, width=60, depth=32, floors_above=3)
    building = generate(params)
    assert building.floor(1).role == "surgery"
    assert any(r.type == "operating_room" for r in building.floor(1).rooms)
    assert building.floor(2).role == "wards"


def test_hotel_bathrooms_open_into_the_guest_room_where_the_unit_is_wide_enough() -> None:
    params = make_params(building_type=BuildingType.HOTEL, width=56, depth=36, floors_above=2)
    floors = generate(params).floors
    pairs = {(a.type, b.type) for floor in floors for a, b in door_pairs(floor)}
    assert ("bathroom", "guest_room") in pairs or ("guest_room", "bathroom") in pairs


@pytest.mark.parametrize("street", list(Side))
def test_shallow_wide_flats_keep_every_room_reachable(street: Side) -> None:
    """No spine: only the hall's neighbours and back[0]'s neighbour can have doors."""
    for seed in range(2):
        params = make_params(
            building_type=BuildingType.APARTMENT,
            width=26,
            depth=26,
            floors_above=2,
            wealth=Wealth.SQUATTER,
            street_side=street,
            seed=seed,
        )
        assert not hard_violations(generate(params))


def test_closets_open_onto_the_hallway() -> None:
    closets = 0
    for width, depth in ((48, 28), (56, 30), (44, 26)):
        for seed in range(4):
            params = make_params(
                building_type=BuildingType.APARTMENT, width=width, depth=depth, seed=seed
            )
            for floor in generate(params).floors:
                for a, b in door_pairs(floor):
                    for closet, other in ((a, b), (b, a)):
                        if closet.type == "closet":
                            assert other.type == "hallway", (width, depth, seed)
                            closets += 1
    assert closets
