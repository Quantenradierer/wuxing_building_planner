"""Fixes from the human review (docs/human-review.md)."""

from collections import Counter

from roomplanner.generator import generate
from roomplanner.model import Building, Cell, Edge, Floor, OpeningKind, PlacedObject
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


def test_one_security_gate_crosses_each_entrance_and_a_guard_desk_stands_in_the_lobby() -> None:
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
        lobby = next(r for r in floor.rooms if r.type == "reception_lobby")
        doors = [
            d
            for d in floor.openings
            if d.kind is OpeningKind.DOOR
            and len(d.edges) >= 3
            and (d.edges[0].cells()[0] in lobby.cells) != (d.edges[0].cells()[1] in lobby.cells)
            and (d.edges[0].cells()[0] in floor.footprint)
            != (d.edges[0].cells()[1] in floor.footprint)
        ]
        assert len(gates) == len(doors) >= 1
        assert any(d.kind == "guard_desk" for d in desks)
        checked += 1
    assert checked == 5


def test_an_office_has_an_executive_office_on_its_top_floor() -> None:
    for seed in range(1, 6):
        building = generate(make_params(width=60, depth=40, floors_above=2, seed=seed))
        top = building.floor(1)
        assert any(r.type == "executive_office" for r in top.rooms)


def test_the_head_of_every_pod_in_a_nap_room_is_at_a_wall() -> None:
    pods = 0
    for seed in range(1, 6):
        for floor in office(seed=seed).floors:
            for pod in (o for o in floor.objects if o.kind == "capsule"):
                back = pod.facing.opposite
                cells = [
                    Cell(x, y)
                    for x in range(pod.x, pod.x + pod.w)
                    for y in range(pod.y, pod.y + pod.h)
                ]
                assert any(Edge.of(c, back) in floor.walls for c in cells), pod
                pods += 1
    assert pods
