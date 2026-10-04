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
from dataclasses import dataclass

from roomplanner.geometry import Cell, Side, rectangle, thinnest_extent
from roomplanner.pipeline.base import AllocationError, PlannedRoom
from roomplanner.pipeline.layout.regions import (
    aspect_ok,
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
NOISE = 0.8  # random share added to the pool's "furthest below its share" score
L_SHAPE = 0.3  # chance that a cut also tries carving a corner off (leaving an L)
MIN_USEFUL = 3  # cells: thinnest part of a region some room could still use
ACCESS = 2  # cells of shared wall with circulation a room needs for a door


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

    # --- entry point ------------------------------------------------------------------

    def run(self, regions: list[frozenset[Cell]], level_name: str) -> list[PlannedRoom]:
        free = list(regions)
        rooms: list[PlannedRoom] = []
        needs = self._needs(sum(len(r) for r in regions))
        pool = [e for e in self.entries if e.fill]
        lows = [self._range(e)[0] for e in pool] + [n.low for n in needs]
        self.min_useful = max(8, min(lows, default=12))
        for need in sorted(needs, key=lambda n: n.rank):
            if not self._place(need, free, rooms):
                message = f"{level_name}: no space for {need.entry.room}"
                if need.required:
                    raise AllocationError(message)
                self.warnings.append(message)
        self._fill(free, pool, rooms)
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
        rooms.append(PlannedRoom(need.entry.room, piece))
        return True

    def _fits(self, cells: frozenset[Cell], need: Need) -> bool:
        return self._fits_spec(cells, need.spec, need.low, need.high)

    def _fits_spec(self, cells: frozenset[Cell], spec: RoomSpec, low: int, high: int) -> bool:
        if not low <= len(cells) <= high * TOLERANCE:
            return False
        if len(cells & self.border) < min(ACCESS, spec.door_width):
            return False
        return aspect_ok(cells, spec) and shape_ok(cells, spec)

    def _score(self, cells: frozenset[Cell], need: Need) -> float:
        score = abs(len(cells) - need.target) / need.target
        score += self._soft(cells, need.spec, need.entry.near)
        return score + self.rng.uniform(0, 0.1)

    def _soft(self, cells: frozenset[Cell], spec: RoomSpec, near: str | None) -> float:
        """Windows and nearness are only preferences."""
        on_facade = not self.outer.isdisjoint(cells)
        score = 0.0
        if spec.windows is WindowRule.REQUIRED and not on_facade:
            score += 0.4
        elif spec.windows is WindowRule.FORBIDDEN and on_facade:
            score += 0.25
        anchor = self.anchors.get(near) if near else None
        if anchor is not None:
            cx = sum(c.x for c in cells) / len(cells)
            cy = sum(c.y for c in cells) / len(cells)
            score += 0.5 * (abs(cx - anchor.x) + abs(cy - anchor.y)) / max(1, self.diagonal)
        return score

    # --- cutting --------------------------------------------------------------------

    def _cut(self, region: frozenset[Cell], need: Need) -> Choice | None:
        """The best piece of `region` for `need`: a straight cut off one end, or a corner."""
        return self._cut_for(region, need.spec, need.low, need.high, need.target, need)

    def _cut_for(
        self,
        region: frozenset[Cell],
        spec: RoomSpec,
        low: int,
        high: int,
        target: int,
        need: Need | None,
        near: str | None = None,
    ) -> Choice | None:
        x0, y0, x1, y1 = bbox(region)
        pieces: list[tuple[frozenset[Cell], float]] = []
        # Two straight cuts: a slab off one end of the region, then a piece off one end of
        # the slab across the first cut. That reaches the corners of big, deep regions.
        for slab in [region, *self._slabs(region, low, high * TOLERANCE * 6, target * 2)]:
            for piece in self._slabs(slab, low, high * TOLERANCE, target):
                pieces.append((piece, 0.0))
            if slab is not region and low <= len(slab) <= high * TOLERANCE:
                pieces.append((slab, 0.0))
        if self.rng.random() < L_SHAPE:
            pieces += self._corners(region, (x0, y0, x1, y1), spec, target)
        best: Choice | None = None
        pieces.sort(key=lambda p: abs(len(p[0]) - target))
        for piece, bonus in pieces[:14]:
            if len(piece) == len(region) or not self._fits_spec(piece, spec, low, high):
                continue
            parts = components(piece)
            if len(parts) != 1:
                continue
            rests = components(region - piece)
            score = abs(len(piece) - target) / target - bonus
            score += self._soft(piece, spec, near if need is None else need.entry.near)
            score += sum(len(r) / 20 for r in rests if not self._useful(r))
            score += self._blind(region - piece) / 20
            score += self.rng.uniform(0, 0.1)
            if best is None or score < best.score:
                best = Choice(piece, rests, score)
        return best

    def _slabs(
        self, region: frozenset[Cell], low: int, cap: float, aim: int
    ) -> list[frozenset[Cell]]:
        """Pieces cut off either end of `region` along either axis, of low..cap cells."""
        found: list[frozenset[Cell]] = []
        per_end = max(3, 12 // 4)
        for vertical in (True, False):
            lines: dict[int, list[Cell]] = {}
            for p in region:
                lines.setdefault(p.x if vertical else p.y, []).append(p)
            for from_low in (True, False):
                taken: set[Cell] = set()
                end: list[frozenset[Cell]] = []
                for key in sorted(lines, reverse=not from_low):
                    taken.update(lines[key])
                    if len(taken) > cap:
                        break
                    if len(taken) >= low and len(taken) < len(region):
                        end.append(frozenset(taken))
                # Spread over the sizes that fit, so thick pieces are among them too.
                step = max(1, len(end) // per_end)
                found += end[::step][:per_end]
        found.sort(key=lambda piece: abs(len(piece) - aim))
        return found

    def _corners(
        self,
        region: frozenset[Cell],
        box: tuple[int, int, int, int],
        spec: RoomSpec,
        target: int,
    ) -> list[tuple[frozenset[Cell], float]]:
        """Rectangles in the corners of the region's box: what is left is an L."""
        x0, y0, x1, y1 = box
        found: list[tuple[frozenset[Cell], float]] = []
        for width in range(spec.min_side, max(spec.min_side, x1 - x0) + 1):
            height = max(spec.min_side, math.ceil(target / width))
            if height > y1 - y0:
                continue
            for x, y in ((x0, y0), (x1 - width, y0), (x0, y1 - height), (x1 - width, y1 - height)):
                piece = rectangle(x, y, width, height)
                if piece <= region:
                    found.append((piece, 0.05))
        return found

    def _blind(self, rest: frozenset[Cell]) -> int:
        """Cells of `rest` with no straight line through `rest` to circulation: the pocket
        behind a room cut off the frontage, which no room could be entered from."""
        border = rest & self.border
        seen = set(border)
        for side in Side:
            dx, dy = side.delta
            reach: set[Cell] = set()
            for cell in sorted(rest, key=lambda c: c.x * dx + c.y * dy, reverse=True):
                if cell in border or cell.neighbour(side) in reach:
                    reach.add(cell)
            seen |= reach
        return len(rest) - len(seen)

    def _useful(self, cells: frozenset[Cell]) -> bool:
        """Could some room still be made of this piece?"""
        return (
            len(cells) >= self.min_useful
            and len(cells & self.border) >= ACCESS
            and thinnest_extent(cells, cells) >= MIN_USEFUL
        )

    # --- the free rest ---------------------------------------------------------------

    def _fill(
        self,
        free: list[frozenset[Cell]],
        pool: list[RoomEntry],
        rooms: list[PlannedRoom],
    ) -> None:
        total = sum(e.weight for e in pool) or 1.0
        while free:
            free.sort(key=lambda r: (-len(r), min(r)))
            region = free.pop(0)
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
                choice = self._cut_for(region, spec, low, high, (low + high) // 2, None, entry.near)
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
                    rooms.append(PlannedRoom(self.filler, region, leftover=True))
                continue
            _, entry, choice = chosen
            self._add(entry, choice.piece, rooms)
            free.extend(choice.rests)

    def _halve(self, region: frozenset[Cell], pool: list[RoomEntry]) -> list[frozenset[Cell]]:
        """A region no room can be cut from, though it is bigger than any room: split it
        across its long side so the halves can be tried again."""
        biggest = max((self._range(e)[1] for e in pool), default=0)
        if len(region) <= biggest * TOLERANCE * 1.5:
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
        return (
            deficit
            + self.rng.uniform(0, NOISE)
            - self._soft(cells, spec, entry.near)
            - (0.5 if entry.priority is Priority.OPTIONAL else 0.0)
        )

    def _add(self, entry: RoomEntry, cells: frozenset[Cell], rooms: list[PlannedRoom]) -> None:
        self.made[id(entry)] = self.made.get(id(entry), 0) + 1
        rooms.append(PlannedRoom(entry.room, cells))
