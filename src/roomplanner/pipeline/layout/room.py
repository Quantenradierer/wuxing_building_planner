"""Room layout: one room of `params.room`, width x depth cells, without the building around it.

For trying out a room type (its furniture, templates, stalls, lights) without generating a
whole building. The footprint is the room; its main entrance opens onto the street side.
Units (flats) are subdivided and stall rooms carved as in a building, with the street as
the circulation they open onto. The other stages run unchanged.
"""

from __future__ import annotations

from roomplanner.geometry import Cell, Side
from roomplanner.params import EntranceKind
from roomplanner.pipeline.base import (
    BuildingPlan,
    Context,
    EntranceRequest,
    FloorPlan,
    PlannedRoom,
)
from roomplanner.pipeline.layout.frame import Band, BandKind, Frame, Interval
from roomplanner.pipeline.layout.stalls import carve_stalls
from roomplanner.pipeline.layout.units import subdivide
from roomplanner.pipeline.registry import register


@register("layout", "room")
class RoomLayout:
    def main_min_depth(self, ctx: Context) -> int:
        return ctx.rules.spec(room_type(ctx)).min_side

    def check_feasibility(self, ctx: Context, footprint: frozenset[Cell]) -> list[str]:
        kind = room_type(ctx)
        if kind not in ctx.rules.rooms:
            known = ", ".join(sorted(ctx.rules.rooms))
            return [f"{ctx.params.building_type} has no room type {kind!r} (known: {known})"]
        if min(ctx.width, ctx.height) < ctx.rules.spec(kind).min_side:
            return [f"{kind} needs at least {ctx.rules.spec(kind).min_side} cells on each side"]
        return []

    def layout(self, ctx: Context, footprint: frozenset[Cell]) -> BuildingPlan:
        kind = room_type(ctx)
        street = ctx.params.street_side
        role, _ = ctx.rules.role_for(0, ctx.params)
        # u along the street facade, v away from it: the street is where a unit's corridor is.
        along_x = street in (Side.N, Side.S)
        frame = Frame(along_x, *((ctx.width, ctx.height) if along_x else (ctx.height, ctx.width)))
        towards = frame.local(street)
        unit = ctx.rules.program.units.get(kind)
        if unit is not None:
            band = Band(0, BandKind.STRIP, 0, frame.depth, towards, facade=True)
            span = Interval(0, frame.length)
            rooms = subdivide(frame, band, span, "0-01", unit, ctx.rules, ctx.rng("layout"))
        else:
            rooms = [PlannedRoom(kind, footprint)]
        facade = [c for c in footprint if c.neighbour(street) not in footprint]
        outside = frozenset(c.neighbour(street) for c in facade)
        rooms = carve_stalls(rooms, ctx.rules, outside)
        # The entrance goes into the unit's entry, the stalls' host or the room itself.
        target = next((i for i, r in enumerate(rooms) if r.entry), None)
        if target is None:
            target = next(i for i, r in enumerate(rooms) if r.host is None)
        middle = frame.length / 2
        hint = min(
            (c for c in facade if c in rooms[target].cells),
            key=lambda c: abs((c.x if along_x else c.y) + 0.5 - middle),
        )
        entrance = EntranceRequest(EntranceKind.MAIN, street, target, hint)
        return BuildingPlan([FloorPlan(0, role, rooms, [entrance])])


def room_type(ctx: Context) -> str:
    assert ctx.params.room is not None, "the room layout needs params.room"
    return ctx.params.room
