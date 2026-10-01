"""Lights, security and condition layers, and annexes (closets entered through a room)."""

from roomplanner.generator import generate
from roomplanner.geometry import Axis, Cell
from roomplanner.model import Building, Floor, Opening, OpeningKind, OpeningState, Room
from roomplanner.params import BuildingType, Condition, EntranceKind, Security
from roomplanner.rules import rules_for
from roomplanner.serialization import from_json, to_json
from roomplanner.validation import hard_violations

from .conftest import make_params


def doors(floor: Floor) -> list[Opening]:
    return [o for o in floor.openings if o.kind is OpeningKind.DOOR]


def is_valid(building: Building) -> bool:
    params = building.params
    return hard_violations(building, rules_for(params.building_type, params.wealth)) == []


def test_every_room_is_lit_and_entrances_have_lights_outside() -> None:
    building = generate(make_params(width=60, depth=40, floors_above=2))
    ground = building.floor(0)
    lit = {light.room for light in ground.lights}
    assert {room.id for room in ground.rooms} <= lit
    outside = [li for li in ground.lights if li.room is None]
    assert outside
    assert all(Cell(int(li.x), int(li.y)) not in ground.footprint for li in outside)


def test_no_security_means_no_devices_and_simple_locks() -> None:
    building = generate(make_params(width=60, depth=40, security=Security.NONE))
    ground = building.floor(0)
    assert ground.devices == ()
    exterior = [d for d in doors(ground) if d.entrance]
    assert {d.lock for d in exterior} == {"mechanical"}
    assert all(d.lock is None for d in doors(ground) if not d.entrance)


def test_aaa_security_locks_down_the_building() -> None:
    building = generate(
        make_params(width=70, depth=44, floors_above=2, floors_below=1, security=Security.AAA)
    )
    assert is_valid(building)
    kinds = {d.kind for f in building.floors for d in f.devices}
    assert {"camera", "alarm_panel"} <= kinds
    ground = building.floor(0)
    assert {d.lock for d in doors(ground) if d.entrance} == {"biometric"}
    assert any(o.kind == "guard_desk" for o in ground.objects)
    assert any(li.kind == "flood" for li in ground.lights)
    basement = building.floor(-1)
    for door in doors(basement):
        a, b = door.edges[0].cells()
        rooms = {r.type for c in (a, b) if (r := basement.room_at(c)) is not None}
        if "server_room" in rooms:
            assert door.lock == "biometric"


def test_pristine_buildings_are_intact() -> None:
    building = generate(make_params(width=60, depth=40, condition=Condition.PRISTINE))
    for floor in building.floors:
        assert all(o.state is OpeningState.INTACT for o in floor.openings)
        assert all(o.kind is not OpeningKind.BREACH for o in floor.openings)
        assert all(li.state == "on" for li in floor.lights)


def test_ruined_buildings_are_damaged_but_reachable() -> None:
    building = generate(
        make_params(width=70, depth=44, floors_above=2, condition=Condition.RUINED, seed=3)
    )
    assert is_valid(building)
    openings = [o for f in building.floors for o in f.openings]
    assert any(o.kind is OpeningKind.BREACH for o in openings)
    assert any(o.state is OpeningState.BROKEN for o in openings)
    assert any(
        o.kind == "rubble_heap" or o.kind == "collapsed_ceiling"
        for f in building.floors
        for o in f.objects
    )
    # No power: only emergency lights may still shine.
    shining = [li for f in building.floors for li in f.lights if li.state != "off"]
    assert all(li.kind == "emergency" for li in shining)
    for floor in building.floors:
        for door in doors(floor):
            if door.state is OpeningState.BLOCKED:
                assert not floor.is_exterior_wall(door.edges[0])


def test_layers_round_trip_through_json() -> None:
    building = generate(
        make_params(width=60, depth=40, security=Security.CORPORATE, condition=Condition.DERELICT)
    )
    assert from_json(to_json(building)) == building


def test_some_storage_rooms_are_closets_entered_through_their_host() -> None:
    found = 0
    for seed in range(12):
        # Without a back door: the floor's one counted storeroom would be the back room.
        building = generate(
            make_params(
                building_type=BuildingType.OFFICE,
                width=60,
                depth=40,
                seed=seed,
                entrances=(EntranceKind.EMERGENCY,),
            )
        )
        ground = building.floor(0)
        rules = rules_for(BuildingType.OFFICE, building.params.wealth)
        for room in ground.rooms:
            if room.type != "storage":
                continue
            neighbours: set[str] = set()
            for door in doors(ground):
                cells = door.edges[0].cells()
                if any(c in room.cells for c in cells):
                    other = next(c for c in cells if c not in room.cells)
                    neighbour = ground.room_at(other)
                    if neighbour is None:  # an exterior (service) door
                        continue
                    neighbours.add(neighbour.type)
            if neighbours and not any(rules.spec(t).circulation for t in neighbours):
                assert neighbours <= set(rules.spec("storage").access)
                found += 1
    assert found > 0


def test_corporate_offices_get_a_security_room() -> None:
    def has_security_room(security: Security) -> bool:
        building = generate(make_params(width=60, depth=40, security=security))
        return any(r.type == "security_room" for r in building.floor(0).rooms)

    assert has_security_room(Security.CORPORATE)
    assert not has_security_room(Security.NONE)


def test_coffin_units_are_pods_along_both_sides_of_an_aisle() -> None:
    building = generate(
        make_params(building_type=BuildingType.COFFIN_BLOCK, width=48, depth=36, seed=6)
    )
    ground = building.floor(0)
    pods = [r for r in ground.rooms if r.type == "coffin_unit"]
    assert len(pods) >= 30
    # The templates' sizes: 2 x 5, a row's last pod 3 x 5.
    assert all(sorted(_box(r)) in ([2, 5], [3, 5]) for r in pods)
    assert all(any(o.room == r.id and o.kind == "bed" for o in ground.objects) for r in pods)
    aisles = {r.id: r for r in ground.rooms if r.type == "coffin_aisle"}
    for door in doors(ground):
        a, b = (ground.room_at(c) for c in door.edges[0].cells())
        if a is None or b is None or "coffin_unit" not in (a.type, b.type):
            continue
        pod, other = (a, b) if a.type == "coffin_unit" else (b, a)
        assert other.id in aisles  # each pod opens onto its aisle
        assert door.swing is not None
        inside = next(c for c in door.edges[0].cells() if c in other.cells)
        assert inside.neighbour(door.swing.towards.opposite) in pod.cells  # the hatch opens out


def _box(room: Room) -> list[int]:
    xs, ys = [c.x for c in room.cells], [c.y for c in room.cells]
    return [max(xs) - min(xs) + 1, max(ys) - min(ys) + 1]


def test_operating_rooms_are_entered_through_their_scrub_room() -> None:
    building = generate(
        make_params(building_type=BuildingType.HOSPITAL, width=72, depth=48, floors_above=3)
    )
    surgery = building.floor(1)
    theatres = [r for r in surgery.rooms if r.type == "operating_room"]
    assert theatres
    for room in theatres:
        neighbours = {
            other.type
            for door in doors(surgery)
            if any(c in room.cells for c in door.edges[0].cells())
            for c in door.edges[0].cells()
            if c not in room.cells and (other := surgery.room_at(c)) is not None
        }
        assert neighbours == {"scrub_room"}


def test_rooms_of_a_template_size_get_the_template_layout() -> None:
    """Toilet stalls of 2 x 3 cells: the wc against the back wall, the door row free."""
    building = generate(make_params(width=60, depth=40, floors_above=3, seed=1))
    checked = 0
    for floor in building.floors:
        for stall in (r for r in floor.rooms if r.type == "stall" and sorted(_box(r)) == [2, 3]):
            (door,) = [d for d in doors(floor) if any(c in stall.cells for c in d.edges[0].cells())]
            front = {c for e in door.edges for c in e.cells() if c in stall.cells}
            assert len(front) == 1
            (wc,) = [o for o in floor.objects if o.room == stall.id]
            assert wc.kind == "wc" and (wc.w, wc.h) == (2, 2)
            (inside,) = front
            across_y = door.edges[0].axis is Axis.H  # a door in a north or south wall
            row = {c for c in stall.cells if (c.y == inside.y if across_y else c.x == inside.x)}
            assert len(row) == 2 and not row & wc.cells
            checked += 1
    assert checked >= 4
