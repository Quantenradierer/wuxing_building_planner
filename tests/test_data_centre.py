"""Data centre: server halls behind mantraps, racks in rows, a NOC on the ground floor."""

import pytest

from roomplanner.generator import generate
from roomplanner.model import Floor, OpeningKind
from roomplanner.params import BuildingType, Security, Wealth

from .conftest import make_params


def _door_neighbours(floor: Floor, room_id: str) -> list[str]:
    """Types of the rooms (or "outside") on the other side of each door of the room."""
    found: list[str] = []
    for opening in floor.openings:
        if opening.kind is not OpeningKind.DOOR:
            continue
        rooms = [floor.room_at(c) for c in opening.edges[0].cells()]
        if any(r is not None and r.id == room_id for r in rooms):
            other = [r for r in rooms if r is None or r.id != room_id]
            found += [r.type if r is not None else "outside" for r in other]
    return found


@pytest.mark.parametrize("seed", range(4))
def test_data_halls_are_entered_only_through_their_mantrap(seed: int) -> None:
    params = make_params(
        building_type=BuildingType.DATA_CENTRE,
        width=60,
        depth=44,
        floors_above=2,
        floors_below=1,
        wealth=list(Wealth)[seed],
        seed=seed,
    )
    building = generate(params)
    halls = [(f, r) for f in building.floors for r in f.rooms if r.type == "data_hall"]
    assert halls
    for floor, hall in halls:
        assert _door_neighbours(floor, hall.id) == ["mantrap"], (floor.level, hall.id)
        racks = [o for o in floor.objects if o.room == hall.id and o.kind == "server_rack"]
        assert len(racks) >= 4, (floor.level, hall.id)


def test_ground_floor_has_noc_security_and_loading_dock() -> None:
    params = make_params(
        building_type=BuildingType.DATA_CENTRE, width=56, depth=40, security=Security.AAA, seed=1
    )
    types = {r.type for r in generate(params).floor(0).rooms}
    assert {"corp_lobby", "noc", "security_room", "loading_dock", "data_hall"} <= types
