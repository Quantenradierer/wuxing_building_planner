"""Furnishing: place furniture and fixtures in rooms following the room catalog's rules.

Objects are placed greedily, rule by rule. A placement is rejected if it leaves the room,
covers another object or the clearance in front of a door, or splits the room's free floor
into separate parts (every free cell stays walkable from the doors).
"""

from __future__ import annotations

import math
import random
from dataclasses import replace

from roomplanner.geometry import Cell, Edge, Side, connected
from roomplanner.model import Floor, OpeningKind, PlacedObject, Room
from roomplanner.pipeline.base import Context
from roomplanner.pipeline.registry import register
from roomplanner.rules import FurnitureRule, ObjectSpec, Placement

type Rect = tuple[int, int, int, int, Side]  # x, y, w, h, facing


@register("furnishing", "rules")
class RulesFurnishing:
    def furnish(self, ctx: Context, floors: list[Floor]) -> list[Floor]:
        furnished: list[Floor] = []
        for floor in floors:
            rng = ctx.rng(f"furnish:{floor.level}")
            objects: list[PlacedObject] = []
            for room in floor.rooms:
                rules = ctx.rules.spec(room.type).furniture
                if rules:
                    objects += _RoomFurnisher(ctx, floor, room, rng).place(rules)
            furnished.append(replace(floor, objects=tuple(objects)))
        return furnished


def ring_is_one_run(free: frozenset[Cell] | set[Cell], x: int, y: int, w: int, h: int) -> bool:
    """True if the free cells around a rectangle form one contiguous run.

    Then covering the rectangle cannot disconnect anything: the free cells it touches stay
    connected along the ring.
    """
    ring = [Cell(cx, y - 1) for cx in range(x - 1, x + w + 1)]
    ring += [Cell(x + w, cy) for cy in range(y, y + h)]
    ring += [Cell(cx, y + h) for cx in range(x + w, x - 2, -1)]
    ring += [Cell(x - 1, cy) for cy in range(y + h - 1, y - 1, -1)]
    flags = [c in free for c in ring]
    if all(flags):
        return True
    runs = sum(1 for i, flag in enumerate(flags) if flag and not flags[i - 1])
    return runs <= 1


class _RoomFurnisher:
    def __init__(self, ctx: Context, floor: Floor, room: Room, rng: random.Random) -> None:
        self.ctx = ctx
        self.floor = floor
        self.room = room
        self.rng = rng
        self.cells = room.cells
        self.clearance = floor.door_clearance(room)
        door_edges = {e for o in floor.openings if o.kind is OpeningKind.DOOR for e in o.edges}
        self.solid = floor.walls - door_edges
        self.taken: set[Cell] = set()
        self.placed: list[PlacedObject] = []
        xs, ys = [c.x for c in room.cells], [c.y for c in room.cells]
        self.box = (min(xs), min(ys), max(xs) + 1, max(ys) + 1)

    def place(self, rules: list[FurnitureRule]) -> list[PlacedObject]:
        for rule in rules:
            spec = self.ctx.rules.objects[rule.object]
            if rule.placement is Placement.ROWS:
                self._rows(rule, spec)
                continue
            low, high = rule.count_range
            count = round(self.rng.randint(low, high) * self.ctx.rules.wealth.furniture)
            if low > 0:
                count = max(1, count)
            for _ in range(count):
                if not self._place_one(rule, spec):
                    break
        return self.placed

    # --- candidates -------------------------------------------------------------------

    def _place_one(self, rule: FurnitureRule, spec: ObjectSpec) -> bool:
        match rule.placement:
            case Placement.WALL:
                candidates = self._against_walls(spec, corners_only=False)
                self.rng.shuffle(candidates)
            case Placement.CORNER:
                candidates = self._against_walls(spec, corners_only=True)
                self.rng.shuffle(candidates)
            case Placement.CENTER:
                cx, cy = (self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2
                candidates = self._anywhere(spec)
                candidates.sort(key=lambda r: abs(r[0] + r[2] / 2 - cx) + abs(r[1] + r[3] / 2 - cy))
            case Placement.SCATTER:
                candidates = self._anywhere(spec)
                self.rng.shuffle(candidates)
            case Placement.NEAR_EXIT:
                exit_ = self._exterior_door()
                if exit_ is None:
                    return False
                ex, ey = exit_
                candidates = self._anywhere(spec)
                candidates.sort(key=lambda r: abs(r[0] + r[2] / 2 - ex) + abs(r[1] + r[3] / 2 - ey))
            case Placement.ROWS:
                return False
        return any(self._try(rule.object, rect) for rect in candidates)

    def _against_walls(self, spec: ObjectSpec, corners_only: bool) -> list[Rect]:
        """Rectangles whose back row lies entirely against a solid wall."""
        along, deep = spec.size
        rects: list[Rect] = []
        for cell in self.cells:
            for side in Side:
                if Edge.of(cell, side) not in self.solid:
                    continue
                rect = _backed(cell, side, along, deep)
                back = _back_row(rect)
                if not all(c in self.cells and Edge.of(c, side) in self.solid for c in back):
                    continue
                if corners_only and not self._in_corner(back, side):
                    continue
                rects.append(rect)
        return sorted(rects)

    def _in_corner(self, back: list[Cell], side: Side) -> bool:
        first, last = back[0], back[-1]
        ends = (Side.W, Side.E) if side in (Side.N, Side.S) else (Side.N, Side.S)
        return Edge.of(first, ends[0]) in self.solid or Edge.of(last, ends[1]) in self.solid

    def _anywhere(self, spec: ObjectSpec) -> list[Rect]:
        along, deep = spec.size
        x0, y0, x1, y1 = self.box
        rects: list[Rect] = []
        for w, h, facing in ((along, deep, Side.S), (deep, along, Side.E)):
            for x in range(x0, x1 - w + 1):
                for y in range(y0, y1 - h + 1):
                    rects.append((x, y, w, h, facing))
        return rects

    def _exterior_door(self) -> tuple[float, float] | None:
        for door in self.floor.openings:
            if door.kind is not OpeningKind.DOOR:
                continue
            a, b = door.edges[0].cells()
            if (a in self.cells and b not in self.floor.footprint) or (
                b in self.cells and a not in self.floor.footprint
            ):
                xs = [e.x for e in door.edges]
                ys = [e.y for e in door.edges]
                return sum(xs) / len(xs), sum(ys) / len(ys)
        return None

    def _rows(self, rule: FurnitureRule, spec: ObjectSpec) -> None:
        """Parallel rows along the room's long axis, with aisles and cross aisles."""
        along, deep = spec.size
        x0, y0, x1, y1 = self.box
        horizontal = (x1 - x0) >= (y1 - y0)
        length, width = (x1 - x0, y1 - y0) if horizontal else (y1 - y0, x1 - x0)
        m = rule.margin
        per_block = max(1, math.floor(12 / along))  # cross aisle roughly every 6 m
        for across in range(m, width - m - deep + 1, deep + rule.aisle):
            position, in_block = m, 0
            while position + along <= length - m:
                if horizontal:
                    rect = (x0 + position, y0 + across, along, deep, Side.S)
                else:
                    rect = (x0 + across, y0 + position, deep, along, Side.E)
                self._try(rule.object, rect)
                position += along
                in_block += 1
                if in_block == per_block:
                    position += rule.aisle
                    in_block = 0

    # --- commit -----------------------------------------------------------------------

    def _try(self, kind: str, rect: Rect) -> bool:
        x, y, w, h, facing = rect
        cells = {Cell(cx, cy) for cx in range(x, x + w) for cy in range(y, y + h)}
        if not cells <= self.cells or cells & self.taken or cells & self.clearance:
            return False
        free = self.cells - self.taken - cells
        if not ring_is_one_run(free, x, y, w, h) and not connected(free):
            return False
        self.taken |= cells
        self.placed.append(PlacedObject(kind, x, y, w, h, facing, self.room.id))
        return True


def _backed(cell: Cell, side: Side, along: int, deep: int) -> Rect:
    """Rectangle with its back row starting at `cell` against the wall on `side`."""
    match side:
        case Side.N:
            return (cell.x, cell.y, along, deep, Side.S)
        case Side.S:
            return (cell.x, cell.y - deep + 1, along, deep, Side.N)
        case Side.W:
            return (cell.x, cell.y, deep, along, Side.E)
        case Side.E:
            return (cell.x - deep + 1, cell.y, deep, along, Side.W)


def _back_row(rect: Rect) -> list[Cell]:
    x, y, w, h, facing = rect
    match facing:
        case Side.S:  # wall to the north
            return [Cell(cx, y) for cx in range(x, x + w)]
        case Side.N:
            return [Cell(cx, y + h - 1) for cx in range(x, x + w)]
        case Side.E:  # wall to the west
            return [Cell(x, cy) for cy in range(y, y + h)]
        case Side.W:
            return [Cell(x + w - 1, cy) for cy in range(y, y + h)]
