"""Assigning room types to the free regions of a floor, cutting regions where needed.

The floor's free space (what corridor, core and lobby leave) is a few large regions. The
rooms the floor must have (required and counted rooms of its role) are placed first, the
biggest first: each takes the free region that fits best, or a piece cut from a larger one.
What is left is typed from the role's pool of `fill` rooms: the type furthest below its
share of the mix, plus noise, among those that fit. A region too big for any type is cut
further, a piece too small or too thin for every type becomes a leftover (absorbed later).
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass

from roomplanner.bitgrid import BitGrid
from roomplanner.geometry import Cell, Side
from roomplanner.pipeline.base import AllocationError, PlannedRoom
from roomplanner.pipeline.layout.regions import (
    bbox,
    components,
    shape_ok,
    size_class,
)
from roomplanner.rules import (
    Priority,
    RoomEntry,
    RoomSpec,
    Rules,
    WindowRule,
    evaluate,
)

TOLERANCE = 1.4  # a room may be this much bigger than its maximum area
ELASTIC = 3  # an open office is drawn from at most this many times its minimum area
STARVED = 3.0  # penalty for a room that needs a window where there is none
NOISE = 0.8  # random share added to the pool's "furthest below its share" score
L_SHAPE = 0.3  # chance that a cut also tries carving a corner off (leaving an L)
MAX_CORNERS = 6  # a room has at most this many corners (an L has 6)
UNLIMITED = 1 << 30  # corners: no limit
MIN_USEFUL = 3  # cells: thinnest part of a region some room could still use
ACCESS = 2  # cells of shared wall with circulation a room needs for a door
ALIGNED = 0.12  # score bonus per end of a cut that continues a wall between two placed rooms
CLUSTER = 1.8  # score bonus for a type whose placed neighbours are all of that type
SAME_SIZE = 1.0  # score bonus for a piece of a size another room of its type already has
GROWTH_SLACK = 1.6  # each further round lets rooms outgrow their size and aspect limits more
MAX_GROWTH = 8  # cells: thickest strip of leftover a room beside it grows over
TANGLED = 0.15  # score penalty per corner a rest has over MAX_CORNERS
STRAND = 6  # a rest no room can use costs its cells over this (unreachable ones end as sleep pods)
JOG = 0.1  # score penalty per end of a cut that misses such a wall by 1-2 cells
_ACROSS = {
    Side.N: (Side.E, Side.W),
    Side.S: (Side.E, Side.W),
    Side.E: (Side.N, Side.S),
    Side.W: (Side.N, Side.S),
}


@dataclass
class Need:
    entry: RoomEntry
    spec: RoomSpec
    low: int
    high: int
    target: int
    required: bool

    @property
    def rank(self) -> tuple[int, int, int]:
        order = {Priority.REQUIRED: 0, Priority.NORMAL: 1, Priority.OPTIONAL: 2}
        return order[self.entry.priority], -size_class(self.high), -self.low


@dataclass
class Choice:
    piece: frozenset[Cell]
    rests: list[frozenset[Cell]]
    score: float


class Assigner:
    def __init__(
        self,
        rules: Rules,
        role_rooms: list[RoomEntry],
        values: dict[str, int],
        footprint: frozenset[Cell],
        access: frozenset[Cell],
        anchors: dict[str, Cell],
        filler: str,
        rng: random.Random,
        max_corners: int = MAX_CORNERS,
    ) -> None:
        self.rules = rules
        self.entries = [e for e in role_rooms if e.place is None and evaluate(e.when, values)]
        self.footprint = footprint
        self.access = access
        # Cells beside circulation, and cells on the outer wall: set lookups instead of loops.
        self.border = frozenset(c.neighbour(s) for c in access for s in Side) - access
        self.outer = frozenset(
            c for c in footprint for s in Side if c.neighbour(s) not in footprint
        )
        self.anchors = anchors
        self.filler = filler
        self.rng = rng
        self.diagonal = sum(bbox(footprint)[2:]) - sum(bbox(footprint)[:2])
        self.warnings: list[str] = []
        self.made: dict[int, int] = {}
        self.min_useful = 12
        self.max_corners = max_corners
        x0, y0, x1, y1 = bbox(footprint)
        self.grid = BitGrid(x0, y0, x1, y1)
        self.border_m = self.grid.mask(self.border)
        self.outer_m = self.grid.mask(self.outer)
        self.access_m = self.grid.mask(access)
        self._mask_cache: dict[frozenset[Cell], int] = {}
        self._rooms = 0
        self.owner: dict[Cell, int] = {}  # cell -> index of the placed room holding it
        self.kinds: dict[int, str] = {}  # index of a placed room -> its type
        self.sizes: dict[str, set[tuple[int, int]]] = {}  # type -> (w, h) of its rectangles
        self._useful_cache: dict[int, bool] = {}

    # --- entry point ------------------------------------------------------------------

    def run(self, regions: list[frozenset[Cell]], level_name: str) -> list[PlannedRoom]:
        free = list(regions)
        rooms: list[PlannedRoom] = []
        needs = self._needs(sum(len(r) for r in regions))
        pool = [e for e in self.entries if e.fill]
        lows = [self._range(e)[0] for e in pool] + [n.low for n in needs]
        self.min_useful = max(8, min(lows, default=12))
        self._useful_cache.clear()
        for need in sorted(needs, key=lambda n: n.rank):
            if not self._place_or_halve(need, free, rooms):
                message = f"{level_name}: no space for {need.entry.room}"
                if need.required:
                    raise AllocationError(message)
                self.warnings.append(message)
        self._fill(free, pool, rooms)
        for slack in (1.0, GROWTH_SLACK, GROWTH_SLACK**2):
            self._grow_into_leftovers(rooms, slack)
        return rooms

    # --- the rooms a floor needs ------------------------------------------------------

    def _range(self, entry: RoomEntry) -> tuple[int, int]:
        low, high = entry.area or self.rules.spec(entry.room).area
        return low, min(high, max(low * ELASTIC, low + 40))

    def _needs(self, area: int) -> list[Need]:
        needs: list[Need] = []
        for entry in self.entries:
            if entry.fill:
                continue
            spec = self.rules.spec(entry.room)
            low, high = self._range(entry)
            if entry.share is not None:
                count = max(1, math.ceil(entry.share * area / high))
                target = max(low, round(entry.share * area / count))
            else:
                count = self.rng.randint(*entry.count_range)
                target = 0
            for _ in range(count):
                size = target or self.rng.randint(low, high)
                needs.append(
                    Need(entry, spec, low, high, size, entry.priority is Priority.REQUIRED)
                )
        kinds = {n.entry.room for n in needs}
        return [n for n in needs if not n.entry.requires or kinds & set(n.entry.requires)]

    # --- placing --------------------------------------------------------------------

    def _place(self, need: Need, free: list[frozenset[Cell]], rooms: list[PlannedRoom]) -> bool:
        best: tuple[float, frozenset[Cell], list[frozenset[Cell]], int] | None = None
        for index, region in enumerate(free):
            if self._fits(region, need):
                score = self._score(region, need)
                if best is None or score < best[0]:
                    best = (score, region, [], index)
        cuttable = sorted(
            (i for i, r in enumerate(free) if len(r) >= need.low and not self._fits(r, need)),
            key=lambda i: len(free[i]),
        )
        tried = 0
        for index in cuttable:
            choice = self._cut(free[index], need)
            if choice is None:
                continue
            if best is None or choice.score < best[0]:
                best = (choice.score, choice.piece, choice.rests, index)
            tried += 1
            if tried == 4:
                break
        if best is None:
            return False
        _, piece, rests, index = best
        region = free.pop(index)
        free.extend(rests)
        self._own(piece, need.entry.room)
        rooms.append(PlannedRoom(need.entry.room, piece))
        return True

    def _place_or_halve(
        self, need: Need, free: list[frozenset[Cell]], rooms: list[PlannedRoom]
    ) -> bool:
        """Place the need; if no region offers a piece, halve the biggest (a ring-shaped or
        sprawling region has none) and try again."""
        for _ in range(6):
            if self._place(need, free, rooms):
                return True
            biggest = max(free, key=len, default=None)
            halves = self._halve_any(biggest) if biggest is not None else []
            if not halves:
                return False
            free.remove(biggest)  # type: ignore[arg-type]
            free.extend(halves)
        return False

    def _fits(self, cells: frozenset[Cell], need: Need) -> bool:
        return self._fits_spec(cells, need.spec, need.low, need.high)

    def _cheap_fit(self, cells: frozenset[Cell], spec: RoomSpec, low: int, high: int) -> bool:
        return self._cheap_fit_m(self._mask(cells), len(cells), spec, low, high)

    def _cheap_fit_m(self, mask: int, size: int, spec: RoomSpec, low: int, high: int) -> bool:
        """Size, corners, a door's width of wall on circulation, aspect: no shape walk yet."""
        if not low <= size <= high * TOLERANCE:
            return False
        if self.grid.corners(mask) > self.max_corners:
            return False
        if (mask & self.border_m).bit_count() < min(ACCESS, spec.door_width):
            return False
        x0, y0, x1, y1 = self.grid.bbox(mask)
        if (x1 - x0) * (y1 - y0) != size:  # not a rectangle: aspect does not apply
            return True
        long, short = max(x1 - x0, y1 - y0), min(x1 - x0, y1 - y0)
        return long <= spec.max_aspect * short * 1.2

    def _fits_spec(self, cells: frozenset[Cell], spec: RoomSpec, low: int, high: int) -> bool:
        return self._cheap_fit(cells, spec, low, high) and self._shape(cells, spec)

    def _shape(self, cells: frozenset[Cell], spec: RoomSpec) -> bool:
        return self._shape_m(self._mask(cells), spec)

    def _shape_m(self, mask: int, spec: RoomSpec) -> bool:
        """Wide enough: circulation rooms (an open office) count together with the corridor
        beside them, as the validator does."""
        if spec.rectangular:
            x0, y0, x1, y1 = self.grid.bbox(mask)
            if (x1 - x0) * (y1 - y0) != mask.bit_count():
                return False
        if spec.circulation:
            return self.grid.thick(mask, mask | self.access_m, spec.min_side)
        if self.grid.thick(mask, mask, spec.min_side):
            return True
        return shape_ok(self.grid.cells(mask), spec)  # a rectangle with alcoves may still pass

    def _score(self, cells: frozenset[Cell], need: Need) -> float:
        score = abs(len(cells) - need.target) / need.target
        score += self._soft(cells, need.spec, need.entry.near)
        return score + self.rng.uniform(0, 0.1)

    def _soft(self, cells: frozenset[Cell], spec: RoomSpec, near: str | None) -> float:
        """Windows and nearness are only preferences."""
        return self._soft_with(not self.outer.isdisjoint(cells), lambda: cells, spec, near)

    def _soft_m(self, mask: int, spec: RoomSpec, near: str | None) -> float:
        return self._soft_with(bool(mask & self.outer_m), lambda: self.grid.cells(mask), spec, near)

    def _soft_with(
        self,
        on_facade: bool,
        cells: Callable[[], frozenset[Cell]],
        spec: RoomSpec,
        near: str | None,
    ) -> float:
        score = 0.0
        if spec.windows is WindowRule.REQUIRED and not on_facade:
            score += 0.4
        elif spec.windows is WindowRule.FORBIDDEN and on_facade:
            score += 0.25
        anchor = self.anchors.get(near) if near else None
        if anchor is not None:
            members = cells()
            cx = sum(c.x for c in members) / len(members)
            cy = sum(c.y for c in members) / len(members)
            score += 0.5 * (abs(cx - anchor.x) + abs(cy - anchor.y)) / max(1, self.diagonal)
        return score

    # --- cutting --------------------------------------------------------------------

    def _cut(self, region: frozenset[Cell], need: Need) -> Choice | None:
        """The best piece of `region` for `need`: a straight cut off one end, or a corner."""
        return self._cut_for(
            region, need.spec, need.low, need.high, need.target, need, kind=need.entry.room
        )

    def _cut_for(
        self,
        region: frozenset[Cell],
        spec: RoomSpec,
        low: int,
        high: int,
        target: int,
        need: Need | None,
        near: str | None = None,
        kind: str | None = None,
    ) -> Choice | None:
        grid = self.grid
        known: set[tuple[int, int]] = self.sizes.get(kind, set()) if kind else set()
        whole = self._mask(region)
        total = len(region)
        pieces: list[tuple[int, float]] = []
        # Two straight cuts: a slab off one end of the region, then a piece off one end of
        # the slab across the first cut. That reaches the corners of big, deep regions.
        for slab in [whole, *self._slabs(whole, low, high * TOLERANCE * 6, target * 2)]:
            for piece in self._slabs(slab, low, high * TOLERANCE, target):
                pieces.append((piece, 0.0))
            if slab != whole and low <= slab.bit_count() <= high * TOLERANCE:
                pieces.append((slab, 0.0))
        if self.rng.random() < L_SHAPE:
            pieces += self._corners(whole, grid.bbox(whole), spec, target)
        # Cheap checks and a base score first; the connectivity, rests and shape checks
        # (the expensive ones) only for the most promising few.
        near_room = near if need is None else need.entry.near
        scored: list[tuple[float, int]] = []
        seen: set[int] = set()
        for piece, bonus in pieces:
            size = piece.bit_count()
            if piece in seen or size == total:
                continue
            seen.add(piece)
            if not self._cheap_fit_m(piece, size, spec, low, high):
                continue
            base = abs(size - target) / target - bonus
            base += self._soft_m(piece, spec, near_room)
            if known:
                x0, y0, x1, y1 = grid.bbox(piece)
                lit = spec.windows is not WindowRule.REQUIRED or piece & self.outer_m
                if lit and (x1 - x0, y1 - y0) in known and (x1 - x0) * (y1 - y0) == size:
                    base -= SAME_SIZE
            scored.append((base, piece))
        scored.sort(key=lambda s: (s[0], grid.min_cell(s[1])))
        best: tuple[float, int, list[int]] | None = None
        for base, piece in scored[:6]:
            if not self._shape_m(piece, spec):
                continue
            if not grid.connected(piece):
                continue
            rest = whole & ~piece
            rests = grid.components(rest)
            score = base + sum(r.bit_count() / STRAND for r in rests if not self._useful_m(r))
            score += grid.blind(rest, self.border_m) / 20
            score += TANGLED * sum(max(0, grid.corners(r) - self.max_corners) for r in rests)
            score += self._jog(grid.cells(piece), grid.cells(rest))
            score += self.rng.uniform(0, 0.1)
            if best is None or score < best[0]:
                best = (score, piece, rests)
        if best is None:
            return None
        score, piece, rests = best
        return Choice(grid.cells(piece), [grid.cells(r) for r in rests], score)

    def _jog(self, piece: frozenset[Cell], rest: frozenset[Cell]) -> float:
        """Cost of the cut line between `piece` and `rest`: a wall that continues the wall
        between two placed rooms across the region is a bonus, one that misses it by a cell
        or two (a staggered wall: _____-----___) a penalty."""
        if not self.owner:
            return 0.0
        ends: set[tuple[Cell, Cell, Side]] = set()
        for p in piece:
            for s in Side:
                n = p.neighbour(s)
                if n not in rest:
                    continue
                for d in _ACROSS[s]:
                    e, f = p.neighbour(d), n.neighbour(d)
                    if e not in piece or f not in rest:
                        ends.add((e, f, s))
        score = 0.0
        owner = self.owner
        for e, f, s in ends:
            if e in piece or e in rest or f in piece or f in rest:
                continue
            for j in (0, 1, -1, 2, -2):
                a, b = e, f
                for _ in range(abs(j)):
                    side = s if j > 0 else s.opposite
                    a, b = a.neighbour(side), b.neighbour(side)
                ia, ib = owner.get(a), owner.get(b)
                if ia is not None and ib is not None and ia != ib:
                    score += -ALIGNED if j == 0 else JOG
                    break
        return score

    def _slabs(self, region: int, low: int, cap: float, aim: int) -> list[int]:
        """Pieces cut off either end of `region` along either axis, of low..cap cells."""
        found: list[int] = []
        per_end = max(3, 12 // 4)
        total = region.bit_count()
        for vertical in (True, False):
            lines = self.grid.lines(region, vertical)
            for from_low in (True, False):
                ordered = lines if from_low else lines[::-1]
                size = 0
                fits: list[int] = []  # how many lines make a piece of low..cap cells
                for count, (_, _, n) in enumerate(ordered, 1):
                    size += n
                    if size > cap:
                        break
                    if low <= size < total:
                        fits.append(count)
                # Spread over the sizes that fit, so thick pieces are among them too.
                step = max(1, len(fits) // per_end)
                for count in fits[::step][:per_end]:
                    piece = 0
                    for _, line, _ in ordered[:count]:
                        piece |= line
                    found.append(piece)
        found.sort(key=lambda piece: abs(piece.bit_count() - aim))
        return found

    def _corners(
        self,
        region: int,
        box: tuple[int, int, int, int],
        spec: RoomSpec,
        target: int,
    ) -> list[tuple[int, float]]:
        """Rectangles in the corners of the region's box: what is left is an L."""
        x0, y0, x1, y1 = box
        found: list[tuple[int, float]] = []
        for width in range(spec.min_side, max(spec.min_side, x1 - x0) + 1):
            height = max(spec.min_side, math.ceil(target / width))
            if height > y1 - y0:
                continue
            for x, y in ((x0, y0), (x1 - width, y0), (x0, y1 - height), (x1 - width, y1 - height)):
                if x < x0 or y < y0 or x + width > x1 or y + height > y1:
                    continue  # wider than the box: cannot be inside the region
                piece = self.grid.rect(x, y, width, height)
                if not piece & ~region:
                    found.append((piece, 0.05))
        return found

    def _useful_m(self, cells: int) -> bool:
        """Could some room still be made of this piece?"""
        cached = self._useful_cache.get(cells)
        if cached is None:
            cached = self._useful_cache[cells] = (
                cells.bit_count() >= self.min_useful
                and (cells & self.border_m).bit_count() >= ACCESS
                and self.grid.thick(cells, cells, MIN_USEFUL)
            )
        return cached

    def _mask(self, cells: frozenset[Cell]) -> int:
        mask = self._mask_cache.get(cells)
        if mask is None:
            if len(self._mask_cache) > 4096:
                self._mask_cache.clear()
            mask = self._mask_cache[cells] = self.grid.mask(cells)
        return mask

    # --- the free rest ---------------------------------------------------------------

    def _fill(
        self,
        free: list[frozenset[Cell]],
        pool: list[RoomEntry],
        rooms: list[PlannedRoom],
    ) -> None:
        blind: list[frozenset[Cell]] = []
        self._fill_regions(free, pool, rooms, blind)
        # Regions cut off from circulation are reached through a neighbour of their own type
        # (an office behind an office) before they end as leftovers.
        while self._reach_through_neighbour(blind, pool, rooms):
            pass
        self._fill_regions(blind, pool, rooms, None)

    def _has_access(self, region: frozenset[Cell]) -> bool:
        return (self._mask(region) & self.border_m).bit_count() >= ACCESS

    def _reach_through_neighbour(
        self, blind: list[frozenset[Cell]], pool: list[RoomEntry], rooms: list[PlannedRoom]
    ) -> bool:
        """Make the first blind region a room of the type of a placed neighbour it shares a
        door's width of wall with. The door then goes into that neighbour."""
        for region in sorted(blind, key=lambda r: (-len(r), min(r))):
            mask = self._mask(region)
            options: list[tuple[float, RoomEntry]] = []
            for entry in pool:
                spec = self.rules.spec(entry.room)
                low, high = self._range(entry)
                if not self._open(entry) or spec.circulation or not spec.transit:
                    continue
                if not low <= len(region) <= high:
                    continue
                if spec.windows is WindowRule.REQUIRED and self.outer_m & mask == 0:
                    continue
                wall = self._shared_wall(region, entry.room)
                if wall < min(ACCESS, spec.door_width) or not self._shape_m(mask, spec):
                    continue
                options.append((wall + entry.weight, entry))
            if options:
                self._add(max(options, key=lambda o: o[0])[1], region, rooms)
                blind.remove(region)
                return True
        return False

    def _shared_wall(self, region: frozenset[Cell], kind: str) -> int:
        """Cells of `region` that touch a placed room of type `kind`."""
        return sum(
            any(
                (n := c.neighbour(s)) not in region and self.kinds.get(self.owner.get(n, 0)) == kind
                for s in Side
            )
            for c in region
        )

    def _fill_regions(
        self,
        free: list[frozenset[Cell]],
        pool: list[RoomEntry],
        rooms: list[PlannedRoom],
        blind: list[frozenset[Cell]] | None,
    ) -> None:
        """`blind` collects the regions without access to circulation instead of filling them."""
        total = sum(e.weight for e in pool) or 1.0
        while free:
            free.sort(key=lambda r: (-len(r), min(r)))
            region = free.pop(0)
            if blind is not None and not self._has_access(region):
                blind.append(region)
                continue
            options = [e for e in pool if self._open(e)]
            fitting = [e for e in options if self._fits_entry(region, e)]
            normal = [e for e in fitting if e.priority is not Priority.OPTIONAL]
            fitting = normal or fitting
            if fitting:
                entry = max(fitting, key=lambda e: self._wanted(e, region, total, pool))
                self._add(entry, region, rooms)
                continue
            chosen: tuple[float, RoomEntry, Choice] | None = None
            ranked = sorted(options, key=lambda e: -self._wanted(e, region, total, pool))
            found = 0
            for entry in ranked:
                low, high = self._range(entry)
                if len(region) < low:
                    continue
                spec = self.rules.spec(entry.room)
                choice = self._cut_for(
                    region, spec, low, high, (low + high) // 2, None, entry.near, entry.room
                )
                if choice is None:
                    continue
                score = choice.score - self._wanted(entry, choice.piece, total, pool)
                if chosen is None or score < chosen[0]:
                    chosen = (score, entry, choice)
                found += 1
                if found == 4:
                    break
            if chosen is None:
                halves = self._halve(region, pool)
                if halves:
                    free.extend(halves)
                else:
                    self._own(region)
                    rooms.append(PlannedRoom(self.filler, region, leftover=True))
                continue
            _, entry, choice = chosen
            self._add(entry, choice.piece, rooms)
            free.extend(choice.rests)

    def _grow_into_leftovers(self, rooms: list[PlannedRoom], slack: float) -> None:
        """Leftovers cut off from circulation (a strip along a facade behind a row of rooms) are
        long thin sleep pods nobody would build: the rooms beside them grow over them instead,
        each over the part straight behind it, as far as its size and shape allow."""
        for _ in range(4 * len(rooms) + 8):
            left = frozenset(c for r in rooms if r.leftover for c in r.cells)
            if not left:
                break
            best: tuple[float, int, frozenset[Cell]] | None = None
            for index, room in enumerate(rooms):
                if room.leftover or room.unit is not None or room.host is not None:
                    continue
                spec = self.rules.spec(room.type)
                if spec.stalls is not None:
                    continue
                found = self._growth(room, spec, left, slack)
                if found is not None and (best is None or found[0] < best[0]):
                    best = (found[0], index, found[1])
            if best is None:
                break
            _, index, gain = best
            grown = rooms[index]
            rooms[index] = PlannedRoom(
                grown.type, grown.cells | gain, grown.unit, grown.entry, grown.host
            )
            for other, room in enumerate(rooms):
                if room.leftover and not room.cells.isdisjoint(gain):
                    rest = room.cells - gain
                    rooms[other] = PlannedRoom(room.type, rest, leftover=True)
            rooms[:] = [r for r in rooms if r.cells]

    def _growth(
        self, room: PlannedRoom, spec: RoomSpec, left: frozenset[Cell], slack: float
    ) -> tuple[float, frozenset[Cell]] | None:
        """The best strip of `left` straight behind a side of the rectangle `room`: (how full
        the room gets, the strip). Only strips thinner than a room is deep, and only ones
        that leave the room a decent rectangle."""
        x0, y0, x1, y1 = bbox(room.cells)
        if (x1 - x0) * (y1 - y0) != len(room.cells):
            return None
        entry = next((e for e in self.entries if e.room == room.type), None)
        high = self._range(entry)[1] if entry is not None else spec.area[1]
        best: tuple[float, frozenset[Cell]] | None = None
        for side in Side:
            strip: set[Cell] = set()
            for depth in range(1, MAX_GROWTH + 1):
                layer = self._layer(x0, y0, x1, y1, side, depth)
                if not layer <= left:
                    break
                strip |= layer
                w, h = x1 - x0, y1 - y0
                if side in (Side.E, Side.W):
                    w += depth
                else:
                    h += depth
                if w * h > high * TOLERANCE * slack:
                    break
                if max(w, h) > spec.max_aspect * slack * min(w, h):
                    break
                score = w * h / high - 0.1 * depth  # the emptier room, the thicker strip
                if best is None or score < best[0]:
                    best = (score, frozenset(strip))
        return best

    @staticmethod
    def _layer(x0: int, y0: int, x1: int, y1: int, side: Side, depth: int) -> frozenset[Cell]:
        if side is Side.N:
            return frozenset(Cell(x, y0 - depth) for x in range(x0, x1))
        if side is Side.S:
            return frozenset(Cell(x, y1 - 1 + depth) for x in range(x0, x1))
        if side is Side.W:
            return frozenset(Cell(x0 - depth, y) for y in range(y0, y1))
        return frozenset(Cell(x1 - 1 + depth, y) for y in range(y0, y1))

    def _halve(self, region: frozenset[Cell], pool: list[RoomEntry]) -> list[frozenset[Cell]]:
        """A region no room can be cut from, though it is bigger than any room: split it
        across its long side so the halves can be tried again."""
        biggest = max((self._range(e)[1] for e in pool), default=0)
        if len(region) <= biggest * TOLERANCE * 1.5:
            return []
        return self._halve_any(region)

    @staticmethod
    def _halve_any(region: frozenset[Cell]) -> list[frozenset[Cell]]:
        if len(region) < 60:
            return []
        x0, y0, x1, y1 = bbox(region)
        if x1 - x0 >= y1 - y0:
            cut = (x0 + x1) // 2
            parts = [
                frozenset(c for c in region if c.x < cut),
                frozenset(c for c in region if c.x >= cut),
            ]
        else:
            cut = (y0 + y1) // 2
            parts = [
                frozenset(c for c in region if c.y < cut),
                frozenset(c for c in region if c.y >= cut),
            ]
        return [piece for part in parts for piece in components(part)]

    def _open(self, entry: RoomEntry) -> bool:
        return entry.limit is None or self.made.get(id(entry), 0) < entry.limit

    def _fits_entry(self, region: frozenset[Cell], entry: RoomEntry) -> bool:
        low, high = self._range(entry)
        return self._fits_spec(region, self.rules.spec(entry.room), low, high)

    def _wanted(
        self, entry: RoomEntry, cells: frozenset[Cell], total: float, pool: list[RoomEntry]
    ) -> float:
        """How far the type is below its share of the mix, plus noise and soft preferences."""
        made = sum(self.made.values())
        deficit = entry.weight / total * (made + 1) - self.made.get(id(entry), 0)
        spec = self.rules.spec(entry.room)
        # a room that needs a window is not clustered into the interior
        starved = spec.windows is WindowRule.REQUIRED and self.outer.isdisjoint(cells)
        return (
            deficit
            + self.rng.uniform(0, NOISE)
            + (-STARVED if starved else CLUSTER * self._alike(entry.room, cells))
            + (
                SAME_SIZE
                if not starved and self._rect(cells) in self.sizes.get(entry.room, ())
                else 0.0
            )
            - self._soft(cells, spec, entry.near)
            - (0.5 if entry.priority is Priority.OPTIONAL else 0.0)
        )

    @staticmethod
    def _rect(cells: frozenset[Cell]) -> tuple[int, int] | None:
        x0, y0, x1, y1 = bbox(cells)
        return (x1 - x0, y1 - y0) if (x1 - x0) * (y1 - y0) == len(cells) else None

    def _alike(self, kind: str, cells: frozenset[Cell]) -> float:
        """Share of the placed rooms around `cells` that are of type `kind`: rooms of a type
        sit next to each other (a row of meeting rooms, a block of huddle rooms)."""
        around = {
            self.owner[n]
            for c in cells
            for s in Side
            if (n := c.neighbour(s)) not in cells and n in self.owner
        }
        if not around:
            return 0.0
        return sum(self.kinds[i] == kind for i in around) / len(around)

    def _add(self, entry: RoomEntry, cells: frozenset[Cell], rooms: list[PlannedRoom]) -> None:
        self.made[id(entry)] = self.made.get(id(entry), 0) + 1
        self._own(cells, entry.room)
        rooms.append(PlannedRoom(entry.room, cells))

    def _own(self, cells: frozenset[Cell], kind: str = "") -> None:
        self._rooms += 1
        self.kinds[self._rooms] = kind
        if kind and (shape := self._rect(cells)) is not None:
            self.sizes.setdefault(kind, set()).add(shape)
        for cell in cells:
            self.owner[cell] = self._rooms
