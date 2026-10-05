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
from functools import cached_property

from roomplanner.geometry import Cell, Edge, Side, connected
from roomplanner.model import Floor, OpeningKind, PlacedObject, Room
from roomplanner.params import EntranceKind, Wealth
from roomplanner.pipeline.base import Context
from roomplanner.pipeline.registry import register
from roomplanner.rules import FurnitureRule, GroupPart, GroupSpec, ObjectSpec, Placement

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
                if (fixed := template_layout(ctx, floor, room)) is not None:
                    wealth = object_wealth(ctx, room)
                    objects += [replace(o, wealth=wealth) for o in fixed]
                elif rules:
                    clearance = clearances.get(room.id, frozenset())
                    wealth = object_wealth(ctx, room)
                    frame = _RoomFrame(room, clearance, solid, room is hatch, floor.half_cells)
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
        if ctx.rules.program.floor_roles[floor.role].roof is not None:
            return None  # the stairs come out onto the roof
        order = [r for r in floor.rooms if r.type == "stairwell"]
        order += [r for r in floor.rooms if ctx.rules.spec(r.type).circulation]
        return order[0] if order else None


type _Walls = frozenset[tuple[Cell, Side]]
type _Signature = tuple[str, bool, frozenset[Cell], frozenset[Cell], _Walls]
_MIRROR_X = {Side.E: Side.W, Side.W: Side.E, Side.N: Side.N, Side.S: Side.S}
_MIRROR_Y = {Side.N: Side.S, Side.S: Side.N, Side.E: Side.E, Side.W: Side.W}


class _RoomFrame:
    """A room relative to its bounding box, for reusing the layout of an identical room."""

    def __init__(
        self,
        room: Room,
        clearance: frozenset[Cell],
        solid: frozenset[Edge],
        hatch: bool,
        half: frozenset[Cell] = frozenset(),
    ) -> None:
        self.room = room
        xs, ys = [c.x for c in room.cells], [c.y for c in room.cells]
        self.x0, self.y0 = min(xs), min(ys)
        self.w, self.h = max(xs) - self.x0 + 1, max(ys) - self.y0 + 1
        self.cells = frozenset(Cell(c.x - self.x0, c.y - self.y0) for c in room.cells - half)
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
        # Covered by any object; the cells a diagonal cuts count as covered: floor to walk
        # over, but nothing stands in them.
        self.taken: set[Cell] = set(floor.half_cells & room.cells)
        self.blocking: set[Cell] = set()  # covered by objects that can't be walked over
        for obj in existing:
            self.taken |= obj.cells
            if obj.blocking:
                self.blocking |= obj.cells
        self.placed: list[PlacedObject] = []
        self.last_group: Rect | None = None  # where the last group went
        self.access: list[frozenset[Cell]] = []  # cells next to `accessible` objects
        xs, ys = [c.x for c in room.cells], [c.y for c in room.cells]
        self.box = (min(xs), min(ys), max(xs) + 1, max(ys) + 1)

    def place(self, rules: list[FurnitureRule], scale: bool = True) -> list[PlacedObject]:
        """Place objects by rule; `scale`: multiply counts by the wealth tier's factor."""
        tier_factor = self.ctx.rules.wealth.furniture if scale else 1.0
        for rule in rules:
            factor = tier_factor if rule.scale else 1.0
            if rule.choose:
                kinds = [*self._choices(rule), self._main_part(rule.object) or rule.object]
                for kind in kinds:
                    if self._place_one(rule.model_copy(update={"object": kind}), self._spec(kind)):
                        break
                continue
            spec = self._spec(rule.object)
            if rule.placement is Placement.ROWS:
                self._rows(rule, spec)
                continue
            if rule.placement is Placement.AT:
                self._at(rule, spec)
                continue
            low, high = rule.count_range
            if rule.per_room is not None:
                kind, every = rule.per_room
                count = min(high, max(low, -(-self._neighbours(kind) // every)))
            elif rule.per is not None:
                count = round(len(self.cells) / rule.per * factor)
                count = min(high, max(low, count))
            elif low == high:
                count = low  # an exact count (one staircase, one bed) never scales
            else:
                count = round(self.rng.randint(low, high) * factor)
            if low > 0:
                count = max(1, count)
            if rule.head is not None:
                self._pairs(rule, spec, rule.head, count, rule.single)
                continue
            if rule.line:
                self._side_by_side(rule, spec, count)
                continue
            for _ in range(count):
                if self._place_one(rule, spec):
                    continue
                # A group that doesn't fit: at least its main object (the bed, the desk);
                # not on a grid, whose objects must all be alike.
                if rule.placement is Placement.GRID:
                    break
                if (main := self._main_part(rule.object)) is None:
                    break
                alone = rule.model_copy(update={"object": main})
                if not self._place_one(alone, self._spec(main)):
                    break
        return self.placed

    def _pairs(
        self, rule: FurnitureRule, spec: ObjectSpec, head: str, desks: int, single: str | None
    ) -> None:
        """`desks` desks as pairs (`rule.object`) against the walls; a desk left over (odd
        count, or no wall for another pair) goes across the end of a pair (`head`), a lone
        desk as `single`."""
        pairs: list[tuple[Rect, list[PlacedObject]]] = []
        for _ in range(desks // 2):
            before = len(self.placed)
            if not self._place_one(rule, spec) or self.last_group is None:
                break
            pairs.append((self.last_group, self.placed[before:]))
        left = desks - 2 * len(pairs)
        if not pairs and left > 0 and single is not None:
            self._place_one(rule.model_copy(update={"object": single}), self._spec(single))
            return
        for rect, parts in pairs:
            if left <= 0:
                break
            if self._extend(rect, parts, head):
                left -= 1

    def _side_by_side(self, rule: FurnitureRule, spec: ObjectSpec, count: int) -> None:
        """Up to `count` objects touching in a row along one wall: the longest row that fits,
        of those the one nearest the room's entrance (no randomness: rooms of one shape get
        the same row)."""
        walls = self._wall_spots(rule, spec)
        entry = self._entry_cells()
        for n in range(count, 0, -1):
            rows = [[_step(start, k) for k in range(n)] for start in walls]
            rows.sort(key=lambda row: (min(self._distance(r, entry) for r in row), row[0]))
            for row in rows:
                if not all(self._free(rect, rule) for rect in row):
                    continue
                before = list(self.placed), set(self.taken), set(self.blocking)
                if all(self._try(rule.object, rect, spec.walkable) for rect in row):
                    return
                self.placed, self.taken, self.blocking = before  # one of them cut the room

    def _free(self, rect: Rect, rule: FurnitureRule) -> bool:
        """Inside the room against an allowed solid wall, on free floor, off the clearances."""
        x, y, w, h, _ = rect
        cells = {Cell(cx, cy) for cx in range(x, x + w) for cy in range(y, y + h)}
        if not cells <= self.cells or cells & (self.taken | self.clearance):
            return False
        return self._backed_allowed(rect, rule)

    def _wall_spots(self, rule: FurnitureRule, spec: ObjectSpec) -> list[Rect]:
        """Wall spots for `spec`, without those backed against a `not_against` room."""
        return [
            r
            for r in self._against_walls(spec, corners_only=False)
            if self._backed_allowed(r, rule)
        ]

    def _backed_allowed(self, rect: Rect, rule: FurnitureRule) -> bool:
        if not rule.not_against:
            return True
        facing = rect[4]
        for cell in _back_row(rect):
            other = self.floor.room_at(cell.neighbour(facing.opposite))
            if other is not None and other.type in rule.not_against:
                return False
        return True

    def _neighbours(self, kind: str) -> int:
        """How many rooms of this type open into the room (the stalls of a public toilet,
        not those of the toilet behind the wall)."""
        found: set[str] = set()
        for door in self.floor.openings:
            if door.kind is not OpeningKind.DOOR:
                continue
            a, b = door.edges[0].cells()
            if a in self.cells or b in self.cells:
                other = self.floor.room_at(b if a in self.cells else a)
                if other is not None and other.type == kind:
                    found.add(other.id)
        return len(found)

    def _entry_cells(self) -> list[Cell]:
        """Inside cells of the doors into circulation or out of the building, else of any."""
        found: list[tuple[bool, Cell]] = []
        for door in self.floor.openings:
            if door.kind is not OpeningKind.DOOR:
                continue
            for edge in door.edges:
                a, b = edge.cells()
                inside, outside = (a, b) if a in self.cells else (b, a)
                if inside not in self.cells or outside in self.cells:
                    continue
                other = self.floor.room_at(outside)
                main = other is None or self.ctx.rules.spec(other.type).circulation
                found.append((main, inside))
        mains = [c for main, c in found if main]
        return mains or [c for _, c in found]

    @staticmethod
    def _distance(rect: Rect, cells: list[Cell]) -> float:
        x, y, w, h, _ = rect
        cx, cy = x + w / 2, y + h / 2
        return min((abs(c.x + 0.5 - cx) + abs(c.y + 0.5 - cy) for c in cells), default=0.0)

    def _extend(self, rect: Rect, parts: list[PlacedObject], kind: str) -> bool:
        """Replace the group at `rect` by the bigger group `kind` with the same back wall."""
        x, y, w, h, facing = rect
        along, deep = self.ctx.rules.groups[kind].size
        match facing:
            case Side.S:
                bigger = (x, y, along, deep, facing)
            case Side.N:
                bigger = (x, y + h - deep, along, deep, facing)
            case Side.E:
                bigger = (x, y, deep, along, facing)
            case Side.W:
                bigger = (x + w - deep, y, deep, along, facing)
        box = {Cell(cx, cy) for cx in range(x, x + w) for cy in range(y, y + h)}
        solid = {c for o in parts if o.blocking for c in o.cells}
        self.placed = [o for o in self.placed if o not in parts]
        self.taken -= box
        self.blocking -= solid
        if self._try(kind, bigger):
            return True
        self.placed += parts
        self.taken |= box
        self.blocking |= solid
        return False

    def _choices(self, rule: FurnitureRule) -> list[str]:
        """`object` and `choose` in the order to try: largest first among those that fit the
        room's box with `clearance` all round, then among those that fit at all (no
        randomness: rooms of one shape get the same table)."""
        options = sorted([rule.object, *rule.choose], key=lambda k: -math.prod(self._spec(k).size))
        x0, y0, x1, y1 = self.box

        def fits(kind: str, margin: int) -> bool:
            short, long = sorted((x1 - x0 - margin, y1 - y0 - margin))
            a, b = sorted(self._spec(kind).size)
            return a <= short and b <= long

        roomy = [k for k in options if fits(k, 2 * rule.clearance)]
        return roomy + [k for k in options if k not in roomy and fits(k, 0)]

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
                candidates = self._wall_spots(rule, spec)
                self.rng.shuffle(candidates)
                if spec.bank is not None:
                    bank = spec.bank
                    candidates.sort(key=lambda r: self._bank_rank(bank, r))
                if rule.spaced:
                    candidates = [r for r in candidates if self._flanks(r) is not None]
                    candidates.sort(
                        key=lambda r: self._line_rank(
                            self._main_part(rule.object) or rule.object, r
                        ),
                    )
                if spec.loose:
                    candidates.sort(key=self._loose_rank)
                if rule.near_room is not None:
                    candidates.sort(key=lambda r: self._room_distance(r, rule.near_room or ""))
                if rule.faces_door:
                    candidates.sort(key=lambda r: -self._faces_door(r))
            case Placement.BACK:
                candidates = self._against_walls(spec, corners_only=False)
                # Equally far (a square wc in a stall): face the door's wall, then the door,
                # so the wcs of a row of stalls all face their doors.
                sides = self._door_sides()
                candidates.sort(
                    key=lambda r: (
                        -self._door_distance(r),
                        r[4] not in sides,
                        -self._faces_door(r),
                        r,
                    )
                )
            case Placement.END:
                candidates = self._against_walls(spec, corners_only=False)
                x0, y0, x1, y1 = self.box
                long_axis = (Side.E, Side.W) if x1 - x0 >= y1 - y0 else (Side.N, Side.S)

                def off_centre(r: Rect) -> float:
                    x, y, w, h, _ = r
                    return abs(x + w / 2 - (x0 + x1) / 2) + abs(y + h / 2 - (y0 + y1) / 2)

                # Facing along the long axis = backed against a short wall; of those the wall
                # whose middle is farthest from the doors, then its middle (not the corner
                # farthest from a door on a long wall).
                middles: dict[Side, Rect] = {}
                for r in candidates:
                    if r[4] not in middles or off_centre(r) < off_centre(middles[r[4]]):
                        middles[r[4]] = r
                wall_distance = {s: self._door_distance(r) for s, r in middles.items()}
                candidates.sort(
                    key=lambda r: (
                        r[4] not in long_axis,
                        -wall_distance[r[4]],
                        off_centre(r),
                        -self._door_distance(r),
                        r,
                    )
                )
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
                if spec.loose:
                    candidates = self._against_walls(spec, corners_only=False)
                    self.rng.shuffle(candidates)
                    candidates.sort(key=self._loose_rank)
                else:
                    candidates = self._sample(spec, SCATTER_TRIES)
            case Placement.GRID:
                candidates = self._grid(spec, rule.aisle, rule.margin)
            case Placement.FACING_EXIT:
                candidates = self._facing_exit(spec, rule.margin)
            case Placement.AXIS:
                candidates = self._on_axis(spec)
            case Placement.PERIMETER:
                candidates = self._along_walls(spec)
                if rule.sideways:
                    candidates += self._along_walls(spec, sideways=True)
            case Placement.NEAR_EXIT:
                exit_ = self._exterior_door()
                if exit_ is None:
                    return False
                ex, ey = exit_
                candidates = self._anywhere(spec)
                candidates.sort(key=lambda r: abs(r[0] + r[2] / 2 - ex) + abs(r[1] + r[3] / 2 - ey))
            case Placement.FILL:
                candidates = self._filling(rule)
            case Placement.ROWS | Placement.AT:
                return False
        for rect in candidates:
            if self._try(rule.object, rect, spec.walkable, rule.accessible):
                if rule.placement is Placement.FACING_EXIT:
                    self.clearance = self.clearance | self._approach(rect)
                if rule.front_clear:
                    self.clearance = self.clearance | self._in_front(rect, rule.front_clear)
                if rule.spaced:
                    self.clearance = self.clearance | (self._flanks(rect) or frozenset())
                return True
        return False

    def _flanks(self, rect: Rect) -> frozenset[Cell] | None:
        """The cells beside a wall-backed object, along the wall; None unless both are free
        room floor (not wall, corner or another object)."""
        x, y, w, h, facing = rect
        if facing in (Side.N, Side.S):
            sides = [[Cell(x - 1, cy) for cy in range(y, y + h)]]
            sides.append([Cell(x + w, cy) for cy in range(y, y + h)])
        else:
            sides = [[Cell(cx, y - 1) for cx in range(x, x + w)]]
            sides.append([Cell(cx, y + h) for cx in range(x, x + w)])
        cells = {c for side in sides for c in side}
        if not cells <= self.cells or cells & self.blocking:
            return None
        return frozenset(cells)

    def _line_rank(self, kind: str, rect: Rect) -> tuple[bool, bool, float]:
        """Sort key for a spaced object: on the wall line and facing of an earlier one of its
        kind first, then facing the same way on another wall, then the nearest spot."""
        x, y, w, h, facing = rect
        earlier = [o for o in self.placed if o.kind == kind]
        same = [o for o in earlier if o.facing == facing]
        if not same:
            return bool(earlier), bool(earlier), 0.0

        def back(px: int, py: int, pw: int, ph: int) -> int:
            """The coordinate of the wall line behind an object."""
            match facing:
                case Side.S:
                    return py
                case Side.N:
                    return py + ph
                case Side.E:
                    return px
                case Side.W:
                    return px + pw

        mine = back(x, y, w, h)
        aligned = any(back(o.x, o.y, o.w, o.h) == mine for o in same)
        cx, cy = x + w / 2, y + h / 2
        near = min(abs(o.x + o.w / 2 - cx) + abs(o.y + o.h / 2 - cy) for o in same)
        return not aligned, False, near

    def _bank_rank(self, bank: str, rect: Rect) -> tuple[bool, float]:
        """Sort key for a banked object: touching the bank first, else the nearest spot (a
        run that ends at a corner goes on round it)."""
        if self._touches_bank(bank, rect):
            return False, 0.0
        objects = self.ctx.rules.objects
        others = [
            (o.x + o.w / 2, o.y + o.h / 2)
            for o in self.placed
            if (spec := objects.get(o.kind)) is not None and spec.bank == bank
        ]
        if not others:
            return True, 0.0
        cx, cy = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
        return True, min(abs(cx - x) + abs(cy - y) for x, y in others)

    def _loose_rank(self, rect: Rect) -> tuple[bool, bool, bool]:
        """Sort key for movable goods: off the door's zone, touching goods already there,
        in a corner (the first of a cluster)."""
        x, y, w, h, _ = rect
        door_zone = any(
            x - 2 < c.x < x + w + 1 and y - 2 < c.y < y + h + 1 for c, _ in self._doors()
        )
        objects = self.ctx.rules.objects
        touching = any(
            (spec := objects.get(o.kind)) is not None
            and spec.loose
            and o.x <= x + w
            and x <= o.x + o.w
            and o.y <= y + h
            and y <= o.y + o.h
            for o in self.placed
        )
        return door_zone, not touching, not self._in_corner(_back_row(rect), rect[4])

    def _touches_bank(self, bank: str, rect: Rect) -> bool:
        """Does `rect` touch, side by side on the same wall line, an object of this bank
        placed facing the same way?"""
        x, y, w, h, facing = rect
        objects = self.ctx.rules.objects
        for o in self.placed:
            if o.facing != facing or (spec := objects.get(o.kind)) is None or spec.bank != bank:
                continue
            if facing in (Side.N, Side.S):
                same_wall = o.y == y if facing is Side.S else o.y + o.h == y + h
                if same_wall and (o.x + o.w == x or x + w == o.x):
                    return True
            else:
                same_wall = o.x == x if facing is Side.E else o.x + o.w == x + w
                if same_wall and (o.y + o.h == y or y + h == o.y):
                    return True
        return False

    def _in_front(self, rect: Rect, depth: int) -> frozenset[Cell]:
        """The room's cells in front of an object, `depth` deep and as wide as it plus
        `depth // 2` on each side: the dance floor before the DJ booth stays free."""
        x, y, w, h, facing = rect
        side = depth // 2
        match facing:
            case Side.S:
                xs, ys = range(x - side, x + w + side), range(y + h, y + h + depth)
            case Side.N:
                xs, ys = range(x - side, x + w + side), range(y - depth, y)
            case Side.E:
                xs, ys = range(x + w, x + w + depth), range(y - side, y + h + side)
            case Side.W:
                xs, ys = range(x - depth, x), range(y - side, y + h + side)
        return frozenset(Cell(cx, cy) for cx in xs for cy in ys) & self.cells

    def _approach(self, rect: Rect) -> frozenset[Cell]:
        """The room's cells in front of an object, its width wide, up to the wall it faces:
        the way from the door to the reception desk stays free."""
        x, y, w, h, facing = rect
        dx, dy = facing.delta
        found: set[Cell] = set()
        if dx == 0:
            for cx in range(x, x + w):
                cy = y + h if dy > 0 else y - 1
                while Cell(cx, cy) in self.cells:
                    found.add(Cell(cx, cy))
                    cy += dy
        else:
            for cy in range(y, y + h):
                cx = x + w if dx > 0 else x - 1
                while Cell(cx, cy) in self.cells:
                    found.add(Cell(cx, cy))
                    cx += dx
        return frozenset(found)

    def _filling(self, rule: FurnitureRule) -> list[Rect]:
        """The room's rectangle at its door (to circulation first), all across, backed against
        the far wall and facing the door; `landing` cells stay free at the door, and it is at
        most `reach` deep. Only doors decide, so a core room gets it at the same spot on
        every floor. An open room without a door (public stairs) takes the open side it is
        deepest from."""
        doors = self._doors() or self._open_sides()
        if not doors:
            return []
        inside, side = doors[0]
        box = self._largest_rect(inside)
        x0, y0, x1, y1 = box
        extent = y1 - y0 if side in (Side.N, Side.S) else x1 - x0
        deep = extent - rule.landing
        if rule.reach is not None:
            deep = min(deep, rule.reach)
        if deep < 1:
            return []
        match side:
            case Side.N:
                return [(x0, y1 - deep, x1 - x0, deep, side)]
            case Side.S:
                return [(x0, y0, x1 - x0, deep, side)]
            case Side.W:
                return [(x1 - deep, y0, deep, y1 - y0, side)]
            case Side.E:
                return [(x0, y0, deep, y1 - y0, side)]

    def _doors(self) -> list[tuple[Cell, Side]]:
        """(cell inside, side of the room) of each door cell, doors into circulation first."""
        found: list[tuple[bool, Cell, Side]] = []
        for door in self.floor.openings:
            if door.kind is not OpeningKind.DOOR:
                continue
            for edge in door.edges:
                a, b = edge.cells()
                inside, outside = (a, b) if a in self.cells else (b, a)
                if inside not in self.cells or outside in self.cells:
                    continue
                other = self.floor.room_at(outside)
                circulation = other is not None and self.ctx.rules.spec(other.type).circulation
                side = next(s for s in Side if inside.neighbour(s) == outside)
                found.append((not circulation, inside, side))
        return [(cell, side) for _, cell, side in sorted(found)]

    def _open_sides(self) -> list[tuple[Cell, Side]]:
        """(cell inside, side of the room) of each edge into another room without a wall,
        the sides with the most room behind them first."""
        x0, y0, x1, y1 = self.box
        found: list[tuple[int, Cell, Side]] = []
        for cell in self.cells:
            for side in Side:
                outside = cell.neighbour(side)
                if outside in self.cells or Edge.of(cell, side) in self.floor.walls:
                    continue
                if self.floor.room_at(outside) is None:
                    continue
                extent = y1 - y0 if side in (Side.N, Side.S) else x1 - x0
                found.append((-extent, cell, side))
        found.sort(key=lambda f: (f[0], f[1], list(Side).index(f[2])))
        return [(cell, side) for _, cell, side in found]

    def _largest_rect(self, cell: Cell) -> tuple[int, int, int, int]:
        """(x0, y0, x1, y1) of the largest rectangle of room cells containing `cell`."""
        bx0, by0, bx1, by1 = self.box
        best = (cell.x, cell.y, cell.x + 1, cell.y + 1)
        for x0 in range(bx0, cell.x + 1):
            for x1 in range(cell.x + 1, bx1 + 1):
                for y0 in range(by0, cell.y + 1):
                    for y1 in range(cell.y + 1, by1 + 1):
                        area = (x1 - x0) * (y1 - y0)
                        if area <= (best[2] - best[0]) * (best[3] - best[1]):
                            continue
                        if all(
                            Cell(x, y) in self.cells for x in range(x0, x1) for y in range(y0, y1)
                        ):
                            best = (x0, y0, x1, y1)
        return best

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

    def _room_distance(self, rect: Rect, room_type: str) -> float:
        """How far a rectangle's centre is from the nearest room of that type on the floor."""
        x, y, w, h, _ = rect
        cx, cy = x + w / 2, y + h / 2
        cells = [c for r in self.floor.rooms if r.type == room_type for c in r.cells]
        return min((abs(c.x + 0.5 - cx) + abs(c.y + 0.5 - cy) for c in cells), default=0.0)

    def _door_distance(self, rect: Rect) -> float:
        """How far a rectangle's centre is from the room's door clearances (deterministic)."""
        if not self.clearance:
            return 0.0
        x, y, w, h, _ = rect
        cx, cy = x + w / 2, y + h / 2
        return min(abs(c.x + 0.5 - cx) + abs(c.y + 0.5 - cy) for c in self.clearance)

    def _door_sides(self) -> set[Side]:
        """The walls of the room that have a door, as sides seen from inside."""
        sides: set[Side] = set()
        for door in self.floor.openings:
            if door.kind is not OpeningKind.DOOR:
                continue
            for edge in door.edges:
                for cell in edge.cells():
                    if cell in self.cells:
                        sides |= {s for s in Side if Edge.of(cell, s) == edge}
        return sides

    def _faces_door(self, rect: Rect) -> float:
        """How much a rectangle's front points towards the door clearances (deterministic)."""
        if not self.clearance:
            return 0.0
        x, y, w, h, facing = rect
        dx = sum(c.x + 0.5 for c in self.clearance) / len(self.clearance) - (x + w / 2)
        dy = sum(c.y + 0.5 for c in self.clearance) / len(self.clearance) - (y + h / 2)
        fx, fy = facing.delta
        return fx * dx + fy * dy

    def _along_walls(self, spec: ObjectSpec, sideways: bool = False) -> list[Rect]:
        """Wall spots wall by wall, in order along each: placed one after another they
        stand side by side, lining the room. `sideways`: lengthwise along the wall instead,
        pointing along it."""
        if sideways:
            along, deep = spec.size
            turned = spec.model_copy(update={"size": (deep, along)})
            rects = self._along_walls(turned)
            return [(x, y, w, h, _SIDEWAYS[facing]) for x, y, w, h, facing in rects]

        def order(rect: Rect) -> tuple[int, int, int, int]:
            x, y, w, h, facing = rect
            if facing in (Side.N, Side.S):  # on a north or south wall: along x
                return (facing is Side.N, y + h if facing is Side.N else y, x, y)
            return (2 + (facing is Side.W), x + w if facing is Side.W else x, y, x)

        return sorted(self._against_walls(spec, corners_only=False), key=order)

    def _on_axis(self, spec: ObjectSpec) -> list[Rect]:
        """Wall spots centred on the biggest object's axis, facing it: the ends of its long
        axis first, then of its short one (a screen at the end of the meeting table)."""
        walls = self._against_walls(spec, corners_only=False)
        solid = [o for o in self.placed if o.blocking]
        if not solid:
            return walls
        target = max(solid, key=lambda o: o.w * o.h)
        cx, cy = target.x + target.w / 2, target.y + target.h / 2
        long_ends = (Side.E, Side.W) if target.w >= target.h else (Side.N, Side.S)
        spots: list[tuple[bool, float, Rect]] = []
        for rect in walls:
            x, y, w, h, facing = rect
            if facing in (Side.E, Side.W):  # on a west or east wall: centred on the y axis
                off, beyond = abs(y + h / 2 - cy), (x < target.x) == (facing is Side.E)
            else:
                off, beyond = abs(x + w / 2 - cx), (y < target.y) == (facing is Side.S)
            if beyond and off <= 1:
                spots.append((facing not in long_ends, off, rect))
        return [rect for *_, rect in sorted(spots)]

    def _anywhere(self, spec: ObjectSpec) -> list[Rect]:
        along, deep = spec.size
        x0, y0, x1, y1 = self.box
        rects: list[Rect] = []
        for w, h, facing in ((along, deep, Side.S), (deep, along, Side.E)):
            for x in range(x0, x1 - w + 1):
                for y in range(y0, y1 - h + 1):
                    rects.append((x, y, w, h, facing))
        return rects

    def _grid(self, spec: ObjectSpec, aisle: int, margin: int) -> list[Rect]:
        """Spots on one lattice centred in the room, all facing alike along its long axis;
        the outer ring first, so tables line the walls and the middle stays free longest."""
        along, deep = spec.size
        x0, y0, x1, y1 = self.box
        w, h = (along, deep) if x1 - x0 >= y1 - y0 else (deep, along)
        facing = Side.S if w == along else Side.E

        def line(start: int, end: int, size: int) -> list[int]:
            length = end - start - 2 * margin
            count = (length + aisle) // (size + aisle)
            if count <= 0:
                return []
            offset = start + margin + (length - count * (size + aisle) + aisle) // 2
            return [offset + k * (size + aisle) for k in range(count)]

        xs, ys = line(x0, x1, w), line(y0, y1, h)
        spots = [
            (min(i, len(xs) - 1 - i, j, len(ys) - 1 - j), j, i)
            for i in range(len(xs))
            for j in range(len(ys))
        ]
        return [(xs[i], ys[j], w, h, facing) for _, j, i in sorted(spots)]

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

    def _facing_exit(self, spec: ObjectSpec, margin: int) -> list[Rect]:
        """Spots on the axis of each exterior door into the room, facing it, nearest first,
        `margin` cells beyond the door's clearance."""
        along, deep = spec.size
        spots: list[Rect] = []
        for door in self.floor.openings:
            if door.kind is not OpeningKind.DOOR:
                continue
            a, b = door.edges[0].cells()
            inside, outside = (a, b) if a in self.cells else (b, a)
            if inside not in self.cells or outside in self.floor.footprint:
                continue
            dx, dy = inside.x - outside.x, inside.y - outside.y
            facing = next(s for s in Side if s.delta == (-dx, -dy))
            start = len(door.edges) + margin
            if dx == 0:  # a door in a horizontal wall: the object stands north or south of it
                centre = sum(e.x for e in door.edges) / len(door.edges) + 0.5
                x = round(centre - along / 2)
                wall = inside.y if dy > 0 else inside.y + 1
                for k in range(start, start + 40):
                    y = wall + k if dy > 0 else wall - k - deep
                    spots.append((x, y, along, deep, facing))
            else:
                centre = sum(e.y for e in door.edges) / len(door.edges) + 0.5
                y = round(centre - along / 2)
                wall = inside.x if dx > 0 else inside.x + 1
                for k in range(start, start + 40):
                    x = wall + k if dx > 0 else wall - k - deep
                    spots.append((x, y, deep, along, facing))
        return spots

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
        # No margin along an open side (an open office's side to the corridor).
        start, end = (Side.N, Side.S) if horizontal else (Side.W, Side.E)
        m0 = 0 if toward is None and self._open_side(start) else m
        m1 = 0 if toward is None and self._open_side(end) else m
        pitch = along + rule.gap
        per_block = max(1, math.floor((rule.block + rule.gap) / pitch))  # cross aisle every `block`
        rows: list[tuple[int, Side]] = []  # offset across the room, facing
        across = m0
        lanes = rule.lanes and toward is None
        if lanes and across + deep + rule.aisle <= width - m1:
            rows.append((across, Side.S))  # facing the first lane
            across += deep + rule.aisle
            while across + deep <= width - m1:
                if across + 2 * deep + rule.aisle <= width - m1:  # back to back between lanes
                    rows += [(across, Side.N), (across + deep, Side.S)]
                    across += 2 * deep + rule.aisle
                else:  # the last row, facing the lane before it
                    rows.append((across, Side.N))
                    across += deep
        while across + deep <= width - m1 and not lanes:
            if rule.paired and toward is None and across + 2 * deep <= width - m1:
                rows += [(across, Side.N), (across + deep, Side.S)]
                across += 2 * deep + rule.aisle
            else:
                rows.append((across, Side.S))
                across += deep + rule.aisle
        positions = [m + p for p in _line(length - 2 * m, along, per_block, rule.aisle, rule.gap)]
        if toward is None and positions:  # centred along the room
            spare = length - m - (positions[-1] + along)
            positions = [p + spare // 2 for p in positions]
        if toward is not None and positions:  # centred, the side aisles equally wide
            half = _line((length - 2 * m - rule.aisle) // 2, along, per_block, rule.aisle, rule.gap)
            if half:  # two mirrored halves with a central aisle
                span = half[-1] + along
                start = (length - 2 * span - rule.aisle) // 2
                right = [length - start - p - along for p in reversed(half)]
                positions = [start + p for p in half] + right
            else:
                shift = (length - positions[-1] - along - m) // 2
                positions = [p + shift for p in positions]

        def rects(shift: int) -> list[Rect]:
            found: list[Rect] = []
            for across, facing in rows:
                for position in positions:
                    if horizontal:
                        rect = (x0 + position, y0 + across + shift, along, deep, facing)
                    else:
                        side = Side.W if facing is Side.N else Side.E
                        rect = (x0 + across + shift, y0 + position, deep, along, side)
                    found.append(rect if toward is None else (*rect[:4], toward))
            return found

        def free(rect: Rect) -> bool:
            x, y, w, h, _ = rect
            cells = {Cell(cx, cy) for cx in range(x, x + w) for cy in range(y, y + h)}
            return cells <= self.cells and not cells & (self.taken | self.clearance)

        # Rows shifted across the spare width to where most fit (clear of doors on one side);
        # of those, against an open side if there is one, else centred.
        tail = rule.aisle if lanes and rows and rows[-1][1] is Side.S else 0
        spare = max(0, width - m1 - (rows[-1][0] + deep + tail)) if rows else 0
        target = 0 if m0 < m1 else spare if m1 < m0 else spare // 2
        if toward is not None:
            target = 0
        shift = max(
            range(spare + 1), key=lambda s: (sum(map(free, rects(s))), -abs(s - target), -s)
        )
        for rect in rects(shift):
            if rule.fill < 1 and self.rng.random() >= rule.fill:
                continue
            if self._try(rule.object, rect, spec.walkable) and rule.front_clear:
                self.clearance = self.clearance | self._in_front(rect, rule.front_clear)

    def _open_side(self, side: Side) -> bool:
        """True if most of the room's box edge on `side` has no wall (open to a corridor)."""
        x0, y0, x1, y1 = self.box
        match side:
            case Side.N:
                edge = [Cell(x, y0) for x in range(x0, x1)]
            case Side.S:
                edge = [Cell(x, y1 - 1) for x in range(x0, x1)]
            case Side.W:
                edge = [Cell(x0, y) for y in range(y0, y1)]
            case Side.E:
                edge = [Cell(x1 - 1, y) for y in range(y0, y1)]
        inside = [c for c in edge if c in self.cells]
        walled = sum(Edge.of(c, side) in self.floor.walls for c in inside)
        return 2 * walled < len(inside)

    def _at(self, rule: FurnitureRule, spec: ObjectSpec) -> None:
        """`count` objects beside every placed `rule.at`, going round its `beside` sides in turn.

        Counts are per target and never scale (a desk has one chair at any wealth).
        """
        low, high = rule.count_range
        along, deep = spec.size
        left = rule.limit if rule.limit is not None else math.inf
        for target in [o for o in self.placed if o.kind == rule.at]:
            count = min(self.rng.randint(low, high), left)
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
            left -= placed

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
        if not free or not self._reachable(free) or not self._accessible(free):
            return False
        self.taken |= box
        self.blocking |= solid
        self.last_group = rect
        for part, (px, py, pw, ph, facing) in zip(group.parts, parts, strict=True):
            blocking = not objects[part.object].walkable
            self.placed.append(
                PlacedObject(part.object, px, py, pw, ph, facing, self.room.id, blocking)
            )
        return True

    @cached_property
    def _open_cells(self) -> frozenset[Cell]:
        """Cells on a side without a wall to the outside of the room (an open office's side
        to the corridor): reachable from there, whatever stands in the room."""
        return frozenset(
            c
            for c in self.cells
            for s in Side
            if (n := c.neighbour(s)) not in self.cells
            and n in self.floor.footprint
            and Edge.of(c, s) not in self.floor.walls
        )

    def _reachable(self, free: set[Cell] | frozenset[Cell]) -> bool:
        """The free floor is one piece, or every piece touches an open side."""
        if connected(free):
            return True
        if not self._open_cells:
            return False
        left = set(free)
        while left:
            start = left.pop()
            piece, stack = {start}, [start]
            while stack:
                cell = stack.pop()
                for side in Side:
                    n = cell.neighbour(side)
                    if n in left:
                        left.remove(n)
                        piece.add(n)
                        stack.append(n)
            if not piece & self._open_cells:
                return False
        return True

    def _access_cells(self, rect: Rect) -> frozenset[Cell]:
        """The room's cells next to an object's front and flanks: not behind it."""
        x, y, w, h, facing = rect
        sides = {
            Side.N: {Cell(cx, y - 1) for cx in range(x, x + w)},
            Side.S: {Cell(cx, y + h) for cx in range(x, x + w)},
            Side.W: {Cell(x - 1, cy) for cy in range(y, y + h)},
            Side.E: {Cell(x + w, cy) for cy in range(y, y + h)},
        }
        del sides[facing.opposite]
        return frozenset(set[Cell]().union(*sides.values())) & self.cells

    def _accessible(self, free: set[Cell] | frozenset[Cell]) -> bool:
        """Every `accessible` object keeps a free cell at its front or a flank."""
        return all(access & free for access in self.access)

    def _try(self, kind: str, rect: Rect, walkable: bool = False, accessible: bool = False) -> bool:
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
            if not ring_is_one_run(free, x, y, w, h) and not self._reachable(free):
                return False
            if not self._accessible(free):
                return False
            if accessible and not self._access_cells(rect) & free:
                return False
            self.blocking |= cells
        self.taken |= cells
        if accessible:
            self.access.append(self._access_cells(rect))
        self.placed.append(PlacedObject(kind, x, y, w, h, facing, self.room.id, not walkable))
        return True


def _line(length: int, along: int, per_block: int, aisle: int, gap: int = 0) -> list[int]:
    """Offsets of objects packed into `length` cells, `gap` cells apart and an aisle after
    every `per_block`."""
    offsets: list[int] = []
    position, in_block = 0, 0
    while position + along <= length:
        offsets.append(position)
        position += along
        in_block += 1
        if in_block == per_block:
            position += aisle
            in_block = 0
        else:
            position += gap
    return offsets


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


def _step(rect: Rect, k: int) -> Rect:
    """`rect` moved `k` times its own width along the wall behind it."""
    x, y, w, h, facing = rect
    if facing in (Side.N, Side.S):
        return (x + k * w, y, w, h, facing)
    return (x, y + k * h, w, h, facing)


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
_SIDEWAYS = {Side.S: Side.E, Side.N: Side.W, Side.E: Side.N, Side.W: Side.S}  # a quarter turn


def _turn(side: Side, facing: Side) -> Side:
    """`side` of a group drawn facing S, once the group faces `facing`."""
    steps = _CLOCKWISE.index(facing) - _CLOCKWISE.index(Side.S)
    return _CLOCKWISE[(_CLOCKWISE.index(side) + steps) % 4]


def template_layout(ctx: Context, floor: Floor, room: Room) -> list[PlacedObject] | None:
    """The objects of the room's template of its size whose door is where the room's is
    (turned to face the door, mirrored if need be); None if no template fits."""
    templates = ctx.rules.spec(room.type).templates
    if not templates:
        return None
    xs, ys = [c.x for c in room.cells], [c.y for c in room.cells]
    x0, y0 = min(xs), min(ys)
    w, h = max(xs) - x0 + 1, max(ys) - y0 + 1
    if len(room.cells) != w * h:
        return None
    doors: set[tuple[Cell, Side]] = set()  # (cell inside, wall) of each door cell
    for door in floor.openings:
        if door.kind is OpeningKind.DOOR:
            for edge in door.edges:
                a, b = edge.cells()
                for inside, outside in ((a, b), (b, a)):
                    if inside in room.cells and outside not in room.cells:
                        side = next(s for s in Side if inside.neighbour(s) == outside)
                        doors.add((inside, side))
    if len({side for _, side in doors}) != 1:
        return None
    facing = next(iter(doors))[1]
    objects = ctx.rules.objects
    for template in templates:
        along, deep = template.size
        if (w, h) != ((along, deep) if facing in (Side.N, Side.S) else (deep, along)):
            continue
        rect = (x0, y0, w, h, facing)
        width = len(doors)
        for group, at in (
            (template, template.door),
            (_mirrored(template, objects), along - template.door - width),
        ):
            spots = {_template_cell(u, deep - 1, along, deep, rect) for u in range(at, at + width)}
            if spots != {cell for cell, _ in doors}:
                continue
            return [
                PlacedObject(
                    part.object, px, py, pw, ph, turned, room.id, not objects[part.object].walkable
                )
                for part, (px, py, pw, ph, turned) in zip(
                    group.parts, group_parts(group, objects, rect), strict=True
                )
            ]
    return None


def _mirrored(group: GroupSpec, objects: dict[str, ObjectSpec]) -> GroupSpec:
    """The group mirrored along its wall (west and east swapped)."""
    along = group.size[0]
    parts: list[GroupPart] = []
    for part in group.parts:
        size = objects[part.object].size
        lw = size[0] if part.facing in (Side.N, Side.S) else size[1]
        facing = {Side.E: Side.W, Side.W: Side.E}.get(part.facing, part.facing)
        parts.append(
            GroupPart(object=part.object, at=(along - part.at[0] - lw, part.at[1]), facing=facing)
        )
    return GroupSpec(size=group.size, parts=parts)


def _template_cell(u: int, v: int, along: int, deep: int, rect: Rect) -> Cell:
    """Cell (u, v) of a group drawn facing S, once placed at `rect` (as `group_parts`)."""
    x0, y0, _, _, facing = rect
    match facing:
        case Side.S:
            return Cell(x0 + u, y0 + v)
        case Side.N:
            return Cell(x0 + along - u - 1, y0 + deep - v - 1)
        case Side.E:
            return Cell(x0 + v, y0 + along - u - 1)
        case Side.W:
            return Cell(x0 + deep - v - 1, y0 + u)


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
