"""Security layer: door locks and materials, devices, floodlights and guard furniture.

Rules come from `data/security.yaml`, one tier per `security` level. An interior door gets
the lock of the room it opens into (its swing side, or the room it swings out of:
`door_opens_out`); exterior doors the tier's exterior
lock; a unit's front door the `unit` entry.
"""

from __future__ import annotations

import random
from dataclasses import replace

from roomplanner.geometry import Cell, Edge, Side
from roomplanner.layer_rules import LockRule, SecurityTier, load_security
from roomplanner.model import Device, Floor, Light, Opening, OpeningKind, PlacedObject, Room
from roomplanner.pipeline.base import Context
from roomplanner.pipeline.furnishing import RoomFurnisher, solid_walls
from roomplanner.pipeline.lights import light_at, outside
from roomplanner.pipeline.registry import register

UNIT_DOOR = "unit"  # security.yaml key for the front doors of units (apartments)


@register("security", "rules")
class RulesSecurity:
    def apply(self, ctx: Context, floors: list[Floor]) -> list[Floor]:
        table = load_security()
        tier = table.tiers[ctx.params.security]
        secured: list[Floor] = []
        for floor in floors:
            rng = ctx.rng(f"security:{floor.level}")
            owner = {cell: room for room in floor.rooms for cell in room.cells}
            openings = tuple(
                _fire_door(table.fire_doors, door, owner, _lock(ctx, tier, floor, door, owner))
                for door in floor.openings
            )
            devices = _Devices(ctx, floor, tier, owner, rng).place()
            lights: list[Light] = []
            if tier.floodlights is not None:
                for door in floor.openings:
                    if door.entrance is not None:
                        x, y = outside(door, 2.0)
                        lights.append(light_at(tier.floodlights, x, y, None))
            floor = replace(
                floor,
                openings=openings,
                devices=(*floor.devices, *devices),
                lights=(*floor.lights, *lights),
            )
            secured.append(replace(floor, objects=_furniture(ctx, floor, tier, rng)))
        return secured


def _lock(
    ctx: Context, tier: SecurityTier, floor: Floor, door: Opening, owner: dict[Cell, Room]
) -> Opening:
    if door.kind is not OpeningKind.DOOR or door.swing is None:
        return door
    rule: LockRule | None
    if door.entrance is not None or floor.is_exterior_wall(door.edges[0]):
        rule = tier.exterior
    else:
        a, b = door.edges[0].cells()
        # The leaf swings into the room the door belongs to.
        inside = a if door.swing.towards in (Side.N, Side.W) else b
        outside_cell = b if inside == a else a
        room, other = owner.get(inside), owner.get(outside_cell)
        # Circulation never owns a door, however big it is: its neighbour's door opens into it.
        if (
            other is not None
            and not ctx.rules.spec(other.type).circulation
            and ctx.rules.spec(other.type).opens_out(other.area)
        ):
            room, other = other, room
        if room is None:
            return door
        if room.unit is None:
            rule = tier.rooms.get(room.type, tier.interior)
        elif other is None or other.unit is None:  # the unit's front door
            rule = tier.rooms.get(UNIT_DOOR)
        else:  # doors inside a flat stay as they are unless their room type is listed
            rule = tier.rooms.get(room.type)
    if rule is None:
        return door
    if rule.lock == "none":
        return replace(door, material=rule.material)
    return replace(door, material=rule.material, lock=rule.lock, rating=rule.rating or tier.rating)


def _fire_door(
    fire_doors: list[str], door: Opening, owner: dict[Cell, Room], locked: Opening
) -> Opening:
    """A door between a room of a fire-door type and any other room: fire-rated leaf. The lock
    stays; a heavier material (security, blast) is kept."""
    if door.kind is not OpeningKind.DOOR or door.entrance is not None:
        return locked
    rooms = (owner.get(cell) for cell in door.edges[0].cells())
    if locked.material in ("security", "blast") or not any(
        room is not None and room.type in fire_doors for room in rooms
    ):
        return locked
    return replace(locked, material="fire")


class _Devices:
    def __init__(
        self,
        ctx: Context,
        floor: Floor,
        tier: SecurityTier,
        owner: dict[Cell, Room],
        rng: random.Random,
    ) -> None:
        self.ctx = ctx
        self.floor = floor
        self.tier = tier
        self.owner = owner
        self.rng = rng
        self.used: set[Cell] = set()
        self.devices: list[Device] = []

    def place(self) -> list[Device]:
        cameras = self.tier.cameras
        for room in self.floor.rooms:
            count = 1 if room.type in cameras.rooms else 0
            if cameras.corridor_per and self.ctx.rules.spec(room.type).circulation:
                count = max(count, room.area // cameras.corridor_per)
            for _ in range(count):
                self._in_corner("camera", room)
            if room.type in self.tier.motion_sensors:
                self._in_corner("motion_sensor", room)
        for door in self.floor.openings:
            if door.entrance is None:
                continue
            inside = next(c for c in door.edges[0].cells() if c in self.floor.footprint)
            room = self.owner[inside]
            if cameras.entrances:
                self._in_corner("camera", room, near=inside)
            if self.tier.alarm_panels:
                self._beside(door, room)
        return self.devices

    def _add(self, kind: str, cell: Cell, facing: Side, room: Room) -> None:
        self.used.add(cell)
        self.devices.append(Device(kind, cell.x, cell.y, facing, room.id, self.tier.rating))

    def _walls(self, cell: Cell) -> list[Side]:
        return [s for s in Side if Edge.of(cell, s) in self.floor.walls]

    def _in_corner(self, kind: str, room: Room, near: Cell | None = None) -> None:
        """A device in a free corner of the room, facing along the longer free direction."""
        corners = [
            c
            for c in sorted(room.cells)
            if c not in self.used
            and len(walls := self._walls(c)) >= 2
            and any(a.axis is not b.axis for a in walls for b in walls)
        ]
        if not corners:
            return
        if near is not None:
            cell = min(corners, key=lambda c: (abs(c.x - near.x) + abs(c.y - near.y), c))
        else:
            others = [Cell(d.x, d.y) for d in self.devices if d.room == room.id]
            if others:
                cell = max(
                    corners,
                    key=lambda c: (min(abs(c.x - o.x) + abs(c.y - o.y) for o in others), c),
                )
            else:
                cell = self.rng.choice(corners)
        walls = self._walls(cell)
        facing = max(
            (s.opposite for s in walls if s.opposite not in walls),
            key=lambda s: (self._reach(cell, s, room), s),
            default=walls[0].opposite,
        )
        self._add(kind, cell, facing, room)

    def _reach(self, cell: Cell, side: Side, room: Room) -> int:
        steps = 0
        while (cell := cell.neighbour(side)) in room.cells:
            steps += 1
        return steps

    def _beside(self, door: Opening, room: Room) -> None:
        """An alarm panel on the inside wall next to the door."""
        assert door.swing is not None
        inward = door.swing.towards.opposite
        insides = [next(c for c in e.cells() if c in room.cells) for e in door.edges]
        along = (Side.W, Side.E) if inward in (Side.N, Side.S) else (Side.N, Side.S)
        for cell in (insides[0].neighbour(along[0]), insides[-1].neighbour(along[1])):
            if cell in room.cells and cell not in self.used:
                self._add("alarm_panel", cell, inward, room)
                return


def _furniture(
    ctx: Context, floor: Floor, tier: SecurityTier, rng: random.Random
) -> tuple[PlacedObject, ...]:
    if not tier.furniture:
        return floor.objects
    clearances = floor.door_clearances()
    solid = solid_walls(floor)
    objects = list(floor.objects)
    for room in floor.rooms:
        rules = tier.furniture.get(room.type)
        if not rules:
            continue
        existing = [o for o in objects if o.room == room.id]
        clearance = clearances.get(room.id, frozenset())
        furnisher = RoomFurnisher(ctx, floor, room, rng, clearance, solid, existing)
        placed = furnisher.place(rules)
        kinds = {o.kind for o in placed}
        missing = [
            r
            for r in rules
            if r.displace and (furnisher.main_part(r.object) or r.object) not in kinds
        ]
        if missing:  # make way: the room starts again without the objects in the way
            gone = {kind for r in missing for kind in r.displace}
            existing = [o for o in existing if o.kind not in gone]
            objects = [o for o in objects if o.room != room.id or o.kind not in gone]
            furnisher = RoomFurnisher(ctx, floor, room, rng, clearance, solid, existing)
            placed = furnisher.place(rules)
        objects += placed
    return tuple(objects)
