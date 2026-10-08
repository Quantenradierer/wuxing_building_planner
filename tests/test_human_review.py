"""Fixes from the human review (docs/human-review.md)."""

from collections import Counter

from roomplanner.generator import generate
from roomplanner.model import Building, Floor, PlacedObject
from roomplanner.params import BuildingType, Security

from .conftest import make_params


def office(**overrides: object) -> Building:
    return generate(make_params(width=60, depth=40, **{"floors_above": 3, **overrides}))


def test_every_nap_room_has_a_locker_for_each_pod() -> None:
    pods = 0
    for seed in range(1, 9):
        floor = generate(
            make_params(room="nap_room", width=10 + seed, depth=8 + seed % 3, seed=seed)
        ).floor(0)
        kinds = Counter(o.kind for o in floor.objects)
        assert kinds["locker"] == kinds["capsule"]
        pods += kinds["capsule"]
    assert pods > 10


def test_a_tower_gets_a_roof_over_its_floors_above() -> None:
    for seed in range(1, 4):
        building = office(seed=seed)
        assert [f.level for f in building.floors] == [0, 1, 2, 3]
        assert building.floors[-1].role == "roof"
        assert building.floors[2].role != "roof"
    assert office(floors_above=1).floors[-1].role != "roof"


def test_floors_of_one_role_are_not_copies() -> None:
    building = office(seed=1)
    walls = [{(e.x, e.y, e.axis) for e in building.floor(level).walls} for level in (1, 2)]
    assert walls[0] != walls[1]


def test_server_rooms_get_small_mantraps_more_often_with_security() -> None:
    def mantraps(security: Security) -> list[tuple[Floor, int]]:
        found: list[tuple[Floor, int]] = []
        for seed in range(1, 11):
            building = generate(
                make_params(
                    building_type=BuildingType.OFFICE,
                    width=60,
                    depth=40,
                    floors_above=2,
                    floors_below=1,
                    security=security,
                    seed=seed,
                )
            )
            for floor in building.floors:
                found += [(floor, len(r.cells)) for r in floor.rooms if r.type == "mantrap"]
        return found

    low, aaa = mantraps(Security.LOW), mantraps(Security.AAA)
    assert len(aaa) > len(low)
    assert all(area <= 12 for _, area in aaa)


def gates_and_desks(floor: Floor) -> tuple[list[PlacedObject], list[PlacedObject]]:
    lobby = next(r for r in floor.rooms if r.type == "reception_lobby")
    objects = [o for o in floor.objects if o.room == lobby.id]
    return (
        [o for o in objects if o.kind == "security_gate"],
        [o for o in objects if o.kind in ("guard_desk", "reception_desk")],
    )


def test_security_gates_flank_the_entrance_lane_and_the_guard_looks_across_it() -> None:
    checked = 0
    for seed in range(1, 6):
        building = generate(
            make_params(
                building_type=BuildingType.OFFICE,
                width=60,
                depth=40,
                security=Security.AAA,
                seed=seed,
            )
        )
        floor = building.floor(0)
        gates, desks = gates_and_desks(floor)
        assert len(gates) == 2
        a, b = gates
        along_y = a.h > a.w
        # side by side at the same depth, as long as the lane is deep
        assert (a.y, a.h) == (b.y, b.h) if along_y else (a.x, a.w) == (b.x, b.w)
        guard = next(d for d in desks if d.kind == "guard_desk")
        reception = next(d for d in desks if d.kind == "reception_desk")
        assert guard.facing.axis != reception.facing.axis  # at right angles
        checked += 1
    assert checked == 5


def test_an_office_has_an_executive_office_on_its_top_floor() -> None:
    for seed in range(1, 6):
        building = generate(make_params(width=60, depth=40, floors_above=2, seed=seed))
        top = building.floor(1)
        assert any(r.type == "executive_office" for r in top.rooms)
