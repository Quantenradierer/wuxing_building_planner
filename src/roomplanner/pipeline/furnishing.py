"""Furnishing: place furniture and fixtures in rooms following the room catalog's rules.

Objects are placed greedily, rule by rule. A placement is rejected if it leaves the room,
covers another object or the clearance in front of a door, or splits the room's free floor
into separate parts (every free cell stays walkable from the doors).
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable
from dataclasses import replace

from roomplanner.geometry import Cell, Edge, Side, connected
from roomplanner.model import Floor, OpeningKind, PlacedObject, Room
from roomplanner.params import EntranceKind, Wealth
from roomplanner.pipeline.base import Context
from roomplanner.pipeline.registry import register
from roomplanner.rules import FurnitureRule, GroupSpec, ObjectSpec, Placement

type Rect = tuple[int, int, int, int, Side]  # x, y, w, h, facing

SCATTER_TRIES = 80  # random positions tried per scattered object


@register("furnishing", "rules")
class RulesFurnishing:
    def furnish(self, ctx: Context, floors: list[Floor]) -> list[Floor]:
        furnished: list[Floor] = []
        # Rooms of one type with the same shape, doors and walls get the same furniture,
        # mirrored if they are mirror images: the building looks designed, not rolled.
        layouts: dict[_Signature, list[Rect]] = {}
        kinds: dict[_Signature, list[tuple[str, bool]]] = {}
        for floor in floors:
            rng = ctx.rng(f"furnish:{floor.level}")
            objects: list[PlacedObject] = []
            clearances = floor.door_clearances()
            solid = solid_walls(floor)
            hatch = self._roof_hatch_room(ctx, floor)
            for room in floor.rooms:
                rules = ctx.rules.spec(room.type).furniture
                if room is hatch:
                    # After the stairs, so they stand where they do on the other floors.
                    rules = [*rules, FurnitureRule(object="roof_hatch", placement=Placement.CORNER)]
                if rules:
                    clearance = clearances.get(room.id, frozenset())
                    wealth = object_wealth(ctx, room)
                    frame = _RoomFrame(room, clearance, solid, room is hatch)
                    reused = frame.reuse(layouts, kinds)
                    if reused is None:
                        furnisher = RoomFurnisher(ctx, floor, room, rng, clearance, solid)
                        placed = furnisher.place(rules)
                        frame.remember(placed, layouts, kinds)
                    else:
                        placed = reused
                    objects += [replace(o, wealth=wealth) for o in placed]
            furnished.append(replace(floor, objects=tuple(objects)))
        return furnished

    @staticmethod
    def _roof_hatch_room(ctx: Context, floor: Floor) -> Room | None:
        """Top floor: the stairwell (or a circulation room) gets the roof hatch."""
        if EntranceKind.ROOF not in ctx.rules.entrances(ctx.params):
            return None
        if floor.level != ctx.params.floors_above - 1:
            return None
        order = [r for r in floor.rooms if r.type == "stairwell"]
        order += [r for r in floor.rooms if ctx.rules.spec(r.type).circulation]
        return order[0] if order else None


type _Signature = tuple[str, bool, frozenset[Cell], frozenset[Cell], frozenset[tuple[Cell, Side]]]
_MIRROR_X = {Side.E: Side.W, Side.W: Side.E, Side.N: Side.N, Side.S: Side.S}
_MIRROR_Y = {Side.N: Side.S, Side.S: Side.N, Side.E: Side.E, Side.W: Side.W}


class _RoomFrame:
    """A room relative to its bounding box, for reusing the layout of an identical room."""

    def __init__(
        self, room: Room, clearance: frozenset[Cell], solid: frozenset[Edge], hatch: bool
    ) -> None:
        self.room = room
        xs, ys = [c.x for c in room.cells], [c.y for c in room.cells]
        self.x0, self.y0 = min(xs), min(ys)
        self.w, self.h = max(xs) - self.x0 + 1, max(ys) - self.y0 + 1
        self.cells = frozenset(Cell(c.x - self.x0, c.y - self.y0) for c in room.cells)
        self.clearance = frozenset(Cell(c.x - self.x0, c.y - self.y0) for c in clearance)
        self.walls = frozenset(
            (Cell(c.x - self.x0, c.y - self.y0), side)
            for c in room.cells
            for side in Side
            if Edge.of(c, side) in solid
        )
        self.hatch = hatch

    def _signature(self, mx: bool, my: bool) -> _Signature:
        def cell(c: Cell) -> Cell:
            return Cell(self.w - 1 - c.x if mx else c.x, self.h - 1 - c.y if my else c.y)

        def side(s: Side) -> Side:
            return _MIRROR_Y[_MIRROR_X[s] if mx else s] if my else (_MIRROR_X[s] if mx else s)

        return (
            self.room.type,
            self.hatch,
            frozenset(cell(c) for c in self.cells),
            frozenset(cell(c) for c in self.clearance),
            frozenset((cell(c), side(s)) for c, s in self.walls),
        )

    def _mirror(self, rect: Rect, mx: bool, my: bool) -> Rect:
        x, y, w, h, facing = rect
        if mx:
            x, facing = self.w - x - w, _MIRROR_X[facing]
        if my:
            y, facing = self.h - y - h, _MIRROR_Y[facing]
        return (x, y, w, h, facing)

    def reuse(
        self, layouts: dict[_Signature, list[Rect]], kinds: dict[_Signature, list[tuple[str, bool]]]
    ) -> list[PlacedObject] | None:
        """The layout of an identical room seen before (maybe mirrored), placed here."""
        for mx in (False, True):
            for my in (False, True):
                key = self._signature(mx, my)
                if key in layouts:
                    placed: list[PlacedObject] = []
                    for rect, (kind, blocking) in zip(layouts[key], kinds[key], strict=True):
                        x, y, w, h, facing = self._mirror(rect, mx, my)
                        placed.append(
                            PlacedObject(
                                kind, x + self.x0, y + self.y0, w, h, facing, self.room.id, blocking
                            )
                        )
                    return placed
        return None

    def remember(
        self,
        placed: list[PlacedObject],
        layouts: dict[_Signature, list[Rect]],
        kinds: dict[_Signature, list[tuple[str, bool]]],
    ) -> None:
        key = self._signature(False, False)
        layouts[key] = [(o.x - self.x0, o.y - self.y0, o.w, o.h, o.facing) for o in placed]
        kinds[key] = [(o.kind, o.blocking) for o in placed]


def object_wealth(ctx: Context, room: Room) -> Wealth | None:
    """The look of a room's objects if its `wealth_shift` moves it off the building's tier."""
    shift = ctx.rules.spec(room.type).wealth_shift
    if shift == 0:
        return None
    tiers = list(Wealth)
    index = tiers.index(ctx.params.wealth) + shift
    wealth = tiers[min(len(tiers) - 1, max(0, index))]
    return None if wealth is ctx.params.wealth else wealth


def solid_walls(floor: Floor) -> frozenset[Edge]:
    """Walls objects can stand against (not doors or breaches)."""
    return floor.walls - {
        e for o in floor.openings if o.kind is not OpeningKind.WINDOW for e in o.edges
    }


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


class RoomFurnisher:
    """Places objects in one room; later layers reuse it to add objects around existing ones."""

    def __init__(
        self,
        ctx: Context,
        floor: Floor,
        room: Room,
        rng: random.Random,
        clearance: frozenset[Cell],
        solid: frozenset[Edge],
        existing: Iterable[PlacedObject] = (),
    ) -> None:
        self.ctx = ctx
        self.floor = floor
        self.room = room
        self.rng = rng
        self.cells = room.cells
        self.clearance = clearance
        self.solid = solid
        self._wall_rects: dict[tuple[int, int, bool], list[Rect]] = {}
        self.taken: set[Cell] = set()  # covered by any object
        self.blocking: set[Cell] = set()  # covered by objects that can't be walked over
        for obj in existing:
            self.taken |= obj.cells
            if obj.blocking:
                self.blocking |= obj.cells
        self.placed: list[PlacedObject] = []
        xs, ys = [c.x for c in room.cells], [c.y for c in room.cells]
        self.box = (min(xs), min(ys), max(xs) + 1, max(ys) + 1)

    def place(self, rules: list[FurnitureRule], scale: bool = True) -> list[PlacedObject]:
        """Place objects by rule; `scale`: multiply counts by the wealth tier's factor."""
        factor = self.ctx.rules.wealth.furniture if scale else 1.0
        for rule in rules:
            spec = self._spec(rule.object)
            if rule.placement is Placement.ROWS:
                self._rows(rule, spec)
                continue
            if rule.placement is Placement.AT:
                self._at(rule, spec)
                continue
            low, high = rule.count_range
            if rule.per is not None:
                count = round(len(self.cells) / rule.per * factor)
                count = min(high, max(low, count))
            elif low == high:
                count = low  # an exact count (one staircase, one bed) never scales
            else:
                count = round(self.rng.randint(low, high) * factor)
            if low > 0:
                count = max(1, count)
            for _ in range(count):
                if self._place_one(rule, spec):
                    continue
                # A group that doesn't fit: at least its main object (the bed, the desk).
                if (main := self._main_part(rule.object)) is None:
                    break
                alone = rule.model_copy(update={"object": main})
                if not self._place_one(alone, self._spec(main)):
                    break
        return self.placed

    def _spec(self, kind: str) -> ObjectSpec:
        """An object's spec; a group is placed like one object of its size."""
        if (group := self.ctx.rules.groups.get(kind)) is not None:
            return ObjectSpec(size=group.size, glyph="+")
        return self.ctx.rules.objects[kind]

    def _main_part(self, kind: str) -> str | None:
        """A group's biggest part, or None for a plain object."""
        if (group := self.ctx.rules.groups.get(kind)) is None:
            return None
        objects = self.ctx.rules.objects
        return max((p.object for p in group.parts), key=lambda o: math.prod(objects[o].size))

    # --- candidates -------------------------------------------------------------------

    def _place_one(self, rule: FurnitureRule, spec: ObjectSpec) -> bool:
        match rule.placement:
            case Placement.WALL:
                candidates = self._against_walls(spec, corners_only=False)
                self.rng.shuffle(candidates)
            case Placement.BACK:
                candidates = self._against_walls(spec, corners_only=False)
                candidates.sort(key=lambda r: (-self._door_distance(r), r))
            case Placement.FIXED:
                # Far corner of the room's box: only the room's shape decides.
                candidates = self._against_walls(spec, corners_only=True)
                x1, y1 = self.box[2], self.box[3]
                candidates.sort(key=lambda r: (-(r[0] + r[2]) - (r[1] + r[3]) + x1 + y1, r))
            case Placement.CORNER:
                candidates = self._against_walls(spec, corners_only=True)
                self.rng.shuffle(candidates)
            case Placement.CENTER:
                cx, cy = (self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2
                candidates = self._anywhere(spec)
                candidates.sort(key=lambda r: abs(r[0] + r[2] / 2 - cx) + abs(r[1] + r[3] / 2 - cy))
            case Placement.SCATTER:
                candidates = self._sample(spec, SCATTER_TRIES)
            case Placement.NEAR_EXIT:
                exit_ = self._exterior_door()
                if exit_ is None:
                    return False
                ex, ey = exit_
                candidates = self._anywhere(spec)
                candidates.sort(key=lambda r: abs(r[0] + r[2] / 2 - ex) + abs(r[1] + r[3] / 2 - ey))
            case Placement.ROWS | Placement.AT:
                return False
        return any(self._try(rule.object, rect, spec.walkable) for rect in candidates)

    def _against_walls(self, spec: ObjectSpec, corners_only: bool) -> list[Rect]:
        """Rectangles whose back row lies entirely against a solid wall (cached per size)."""
        along, deep = spec.size
        key = (along, deep, corners_only)
        if key not in self._wall_rects:
            self._wall_rects[key] = self._find_against_walls(along, deep, corners_only)
        return list(self._wall_rects[key])

    def _find_against_walls(self, along: int, deep: int, corners_only: bool) -> list[Rect]:
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

    def _door_distance(self, rect: Rect) -> float:
        """How far a rectangle's centre is from the room's door clearances (deterministic)."""
        if not self.clearance:
            return 0.0
        x, y, w, h, _ = rect
        cx, cy = x + w / 2, y + h / 2
        return min(abs(c.x + 0.5 - cx) + abs(c.y + 0.5 - cy) for c in self.clearance)

    def _anywhere(self, spec: ObjectSpec) -> list[Rect]:
        along, deep = spec.size
        x0, y0, x1, y1 = self.box
        rects: list[Rect] = []
        for w, h, facing in ((along, deep, Side.S), (deep, along, Side.E)):
            for x in range(x0, x1 - w + 1):
                for y in range(y0, y1 - h + 1):
                    rects.append((x, y, w, h, facing))
        return rects

    def _sample(self, spec: ObjectSpec, tries: int) -> list[Rect]:
        """Random positions (cheaper than shuffling every position of a big room)."""
        along, deep = spec.size
        x0, y0, x1, y1 = self.box
        rects: list[Rect] = []
        for _ in range(tries):
            w, h, facing = (
                (along, deep, Side.S) if self.rng.random() < 0.5 else (deep, along, Side.E)
            )
            if x1 - x0 < w or y1 - y0 < h:
                continue
            x, y = self.rng.randint(x0, x1 - w), self.rng.randint(y0, y1 - h)
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
        """Parallel rows along the room's long axis, with aisles and cross aisles.

        With `toward`, the rows run across the direction of that object instead and all
        face it (pews facing the altar).
        """
        along, deep = spec.size
        x0, y0, x1, y1 = self.box
        horizontal = (x1 - x0) >= (y1 - y0)
        toward: Side | None = None
        if rule.toward is not None:
            target = next((o for o in self.placed if o.kind == rule.toward), None)
            if target is not None:
                toward = target.facing.opposite  # the wall the target stands against
                horizontal = toward in (Side.N, Side.S)
        length, width = (x1 - x0, y1 - y0) if horizontal else (y1 - y0, x1 - x0)
        m = rule.margin
        per_block = max(1, math.floor(rule.block / along))  # cross aisle every `block` cells
        rows: list[tuple[int, Side]] = []  # offset across the room, facing
        across = m
        while across + deep <= width - m:
            if rule.paired and toward is None and across + 2 * deep <= width - m:
                rows += [(across, Side.N), (across + deep, Side.S)]
                across += 2 * deep + rule.aisle
            else:
                rows.append((across, Side.S))
                across += deep + rule.aisle
        positions: list[int] = []
        position, in_block = m, 0
        while position + along <= length - m:
            positions.append(position)
            position += along
            in_block += 1
            if in_block == per_block:
                position += rule.aisle
                in_block = 0
        if toward is not None and positions:  # centred, the side aisles equally wide
            shift = (length - positions[-1] - along - m) // 2
            positions = [p + shift for p in positions]
        for across, facing in rows:
            for position in positions:
                if horizontal:
                    rect = (x0 + position, y0 + across, along, deep, facing)
                else:
                    side = Side.W if facing is Side.N else Side.E
                    rect = (x0 + across, y0 + position, deep, along, side)
                if toward is not None:
                    rect = (*rect[:4], toward)
                self._try(rule.object, rect, spec.walkable)

    def _at(self, rule: FurnitureRule, spec: ObjectSpec) -> None:
        """`count` objects beside every placed `rule.at`, going round its `beside` sides in turn.

        Counts are per target and never scale (a desk has one chair at any wealth).
        """
        low, high = rule.count_range
        along, deep = spec.size
        for target in [o for o in self.placed if o.kind == rule.at]:
            count = self.rng.randint(low, high)
            front, back = target.facing, target.facing.opposite
            flanks = [s for s in Side if s not in (front, back)]
            named = {"front": [front], "back": [back], "flanks": flanks}
            sides = [side for name in rule.beside for side in named[name]]
            spots = {side: self._beside(target, side, along, deep) for side in sides}
            placed = 0
            while placed < count and any(spots.values()):
                for side in sides:
                    while spots[side]:
                        rect = spots[side].pop(0)
                        if self._try(rule.object, rect, spec.walkable):
                            placed += 1
                            break
                    if placed == count:
                        break

    @staticmethod
    def _beside(target: PlacedObject, side: Side, along: int, deep: int) -> list[Rect]:
        """Spots along one side of `target`, facing it, middle ones first."""
        x, y, w, h = target.x, target.y, target.w, target.h
        match side:
            case Side.N:
                spots = [(cx, y - deep, along, deep, Side.S) for cx in range(x, x + w - along + 1)]
            case Side.S:
                spots = [(cx, y + h, along, deep, Side.N) for cx in range(x, x + w - along + 1)]
            case Side.W:
                spots = [(x - deep, cy, deep, along, Side.E) for cy in range(y, y + h - along + 1)]
            case Side.E:
                spots = [(x + w, cy, deep, along, Side.W) for cy in range(y, y + h - along + 1)]
        middle = (len(spots) - 1) / 2
        return [r for _, r in sorted(enumerate(spots), key=lambda ir: abs(ir[0] - middle))]

    # --- commit -----------------------------------------------------------------------

    def _try_group(self, kind: str, rect: Rect) -> bool:
        """Place all parts of a group or none; the group's empty cells stay free floor."""
        x, y, w, h, _ = rect
        box = {Cell(cx, cy) for cx in range(x, x + w) for cy in range(y, y + h)}
        if not box <= self.cells or box & self.taken:
            return False
        group = self.ctx.rules.groups[kind]
        objects = self.ctx.rules.objects
        parts = group_parts(group, objects, rect)
        solid: set[Cell] = set()
        for part, (px, py, pw, ph, _) in zip(group.parts, parts, strict=True):
            if not objects[part.object].walkable:
                solid |= {Cell(cx, cy) for cx in range(px, px + pw) for cy in range(py, py + ph)}
        if solid & self.clearance:
            return False
        free = self.cells - self.blocking - solid
        if not free or not connected(free):
            return False
        self.taken |= box
        self.blocking |= solid
        for part, (px, py, pw, ph, facing) in zip(group.parts, parts, strict=True):
            blocking = not objects[part.object].walkable
            self.placed.append(
                PlacedObject(part.object, px, py, pw, ph, facing, self.room.id, blocking)
            )
        return True

    def _try(self, kind: str, rect: Rect, walkable: bool = False) -> bool:
        if kind in self.ctx.rules.groups:
            return self._try_group(kind, rect)
        x, y, w, h, facing = rect
        cells = {Cell(cx, cy) for cx in range(x, x + w) for cy in range(y, y + h)}
        if not cells <= self.cells or cells & self.taken:
            return False
        if not walkable:
            if cells & self.clearance:
                return False
            free = self.cells - self.blocking - cells
            if not free:  # never fill a room completely
                return False
            if not ring_is_one_run(free, x, y, w, h) and not connected(free):
                return False
            self.blocking |= cells
        self.taken |= cells
        self.placed.append(PlacedObject(kind, x, y, w, h, facing, self.room.id, not walkable))
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


_CLOCKWISE = [Side.N, Side.E, Side.S, Side.W]


def _turn(side: Side, facing: Side) -> Side:
    """`side` of a group drawn facing S, once the group faces `facing`."""
    steps = _CLOCKWISE.index(facing) - _CLOCKWISE.index(Side.S)
    return _CLOCKWISE[(_CLOCKWISE.index(side) + steps) % 4]


def group_parts(group: GroupSpec, objects: dict[str, ObjectSpec], rect: Rect) -> list[Rect]:
    """The parts of a group placed at `rect` (x, y, w, h, facing), as object rectangles."""
    x0, y0, _, _, facing = rect
    along, deep = group.size
    parts: list[Rect] = []
    for part in group.parts:
        size = objects[part.object].size
        lw, lh = size if part.facing in (Side.N, Side.S) else (size[1], size[0])
        u, v = part.at
        match facing:
            case Side.S:
                x, y, w, h = x0 + u, y0 + v, lw, lh
            case Side.N:
                x, y, w, h = x0 + along - u - lw, y0 + deep - v - lh, lw, lh
            case Side.E:
                x, y, w, h = x0 + v, y0 + along - u - lw, lh, lw
            case Side.W:
                x, y, w, h = x0 + deep - v - lh, y0 + u, lh, lw
        parts.append((x, y, w, h, _turn(part.facing, facing)))
    return parts
