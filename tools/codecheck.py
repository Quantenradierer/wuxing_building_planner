"""Generate random buildings and check them against German building-code rules (MBO, ASR, DIN).

    uv run python tools/codecheck.py [count] [--json results.json]

Prints how many buildings pass each rule (see docs/requirements.md, "Building code").
Rules: escape distance <= 35 m (MBO 35), two escape routes (MBO 33; strict, and with rescue
windows on floors 1-7), dead-end corridors <= 15 m, corridor width (ASR A2.3, DIN 18040),
door width >= 0.9 m (DIN 18100), 1.5 x 1.5 m turning space at doors (DIN 18040), stairwell
>= 2.5 x 4.5 m (DIN 18065, 3 m floors), lift car 1.1 x 1.4 m (DIN EN 81-70), window area
>= 1/8 of floor area with 1.5 m tall windows (MBO 47), 8 + 6 m2 per workstation (ASR A1.2),
parking stall >= 2.3 m wide with an aisle in front of 6.5 m, 6.0 m at 2.4 m stalls and 5.5 m
at 2.5 m stalls (MGarVO 4).

1 cell = 0.5 m. Distances use octile moves (diagonals through open corners), i.e. walking
paths; furniture is ignored. Exits: exterior doors on the ground floor, stairwells (and
public stairs) elsewhere; roof floors are skipped. A dead end is the stretch where the
routes to the two nearest exits overlap (the whole route on a single-exit floor). The
sample is fixed (rng 2026): 100 buildings cycle through all types with random size,
floors, wealth, security and shape; infeasible ones are retried 20 % bigger.
"""

from __future__ import annotations

import heapq
import json
import math
import random
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

from roomplanner.errors import RoomplannerError
from roomplanner.generator import generate
from roomplanner.geometry import Cell, Edge, Side, largest_rectangle
from roomplanner.model import Building, Floor, OpeningKind
from roomplanner.params import BuildingType, GenerationParams, Security, Shape, Wealth
from roomplanner.rules import rules_for

SMALL = {
    "gun_shop",
    "talismonger",
    "ramen_bar",
    "boutique",
    "deli",
    "safehouse",
    "hacker_den",
    "mage_flat",
    "stuffer_shack",
    "dive_bar",
    "chop_shop",
    "street_doc",
    "cosmetic_clinic",
}
WORK_DESKS = {"desk", "office_desk", "executive_desk", "console_desk"}
SIDES = list(Side)
STAIRS = {"stairwell", "public_stairs"}


def configs(n: int = 100) -> list[dict]:
    rng = random.Random(2026)
    types = [t.value for t in BuildingType]
    out = []
    for i in range(n):
        t = types[i % len(types)]
        small = t in SMALL
        out.append(
            {
                "building_type": t,
                "width": rng.randint(18, 32) if small else rng.randint(36, 80),
                "depth": rng.randint(14, 26) if small else rng.randint(24, 56),
                "floors_above": rng.randint(1, 2) if small else rng.randint(1, 5),
                "floors_below": rng.choice([0, 0, 0, 1]),
                "wealth": rng.choice(list(Wealth)).value,
                "security": rng.choice(list(Security)).value,
                "shape": Shape.RECTANGLE.value if rng.random() < 0.7 else Shape.IRREGULAR.value,
                "seed": rng.randint(1, 10_000),
            }
        )
    return out


def build(cfg: dict) -> Building | None:
    cfg = dict(cfg)
    for _ in range(6):
        try:
            return generate(GenerationParams(**cfg))
        except RoomplannerError:
            cfg["width"] = int(cfg["width"] * 1.2) + 2
            cfg["depth"] = int(cfg["depth"] * 1.2) + 2
    return None


class Grid:
    def __init__(self, floor: Floor) -> None:
        self.floor = floor
        self.cells = floor.footprint
        self.walls = floor.walls
        self.passable = floor.passable_edges()
        self.owner = {c: r for r in floor.rooms for c in r.cells}

    def open(self, a: Cell, b: Cell) -> bool:
        if b not in self.cells:
            return False
        e = Edge.between(a, b)
        return e not in self.walls or e in self.passable

    def neighbours(self, c: Cell):
        for s in SIDES:
            n = c.neighbour(s)
            if self.open(c, n):
                yield n, 1.0
        for dx, dy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            h, v, d = Cell(c.x + dx, c.y), Cell(c.x, c.y + dy), Cell(c.x + dx, c.y + dy)
            if self.open(c, h) and self.open(h, d) and self.open(c, v) and self.open(v, d):
                yield d, math.sqrt(2)

    def dist(self, sources: set[Cell]) -> dict[Cell, float]:
        best = {s: 0.0 for s in sources}
        heap = [(0.0, s) for s in sources]
        while heap:
            d, c = heapq.heappop(heap)
            if d > best[c]:
                continue
            for n, w in self.neighbours(c):
                nd = d + w
                if nd < best.get(n, math.inf):
                    best[n] = nd
                    heapq.heappush(heap, (nd, n))
        return best


def exits(floor: Floor, grid: Grid) -> list[set[Cell]]:
    """Ground floor: each exterior door (the cells inside it). Other floors: each stairwell."""
    if floor.level == 0:
        out = []
        for o in floor.openings:
            if o.kind is OpeningKind.DOOR and o.passable and floor.is_exterior_wall(o.edges[0]):
                out.append({c for e in o.edges for c in e.cells() if c in floor.footprint})
        return out
    return [set(r.cells) for r in floor.rooms if r.type in STAIRS]


def square_width(cells: frozenset[Cell], cap: int = 4) -> int:
    """Narrowest point: min over cells of the largest k x k square (<= cap) covering the cell."""
    cover = {c: 0 for c in cells}
    for k in range(1, cap + 1):
        for c in cells:
            sq = [Cell(c.x + i, c.y + j) for i in range(k) for j in range(k)]
            if all(s in cells for s in sq):
                for s in sq:
                    cover[s] = max(cover[s], k)
    return min(cover.values()) if cover else 0


def free_square_at(cell: Cell, room_cells: frozenset[Cell], blocked: set[Cell], k: int = 3) -> bool:
    for ox in range(k):
        for oy in range(k):
            sq = [Cell(cell.x - ox + i, cell.y - oy + j) for i in range(k) for j in range(k)]
            if all(s in room_cells and s not in blocked for s in sq):
                return True
    return False


def check(b: Building) -> dict:
    p = b.params
    rules = rules_for(p.building_type, p.wealth, p.security)

    def spec(t: str):
        try:
            return rules.spec(t)
        except KeyError:
            return None

    r: dict = {
        "type": p.building_type.value,
        "size": f"{b.width}x{b.height}",
        "floors": len(b.floors),
    }
    max_escape, max_dead, worst_corr = 0.0, 0.0, 9
    two_strict = two_lenient = True
    doors_total = doors_narrow = doors_narrow_nonstall = 0
    turn_total = turn_fail = 0
    stairs_ok = elev_ok = True
    stair_dims: list[tuple[int, int]] = []
    win_rooms = win_fail = win_none = 0
    desk_rooms = desk_fail = 0
    park_cars = park_pitch_fail = park_aisle_fail = 0
    fails = defaultdict(list)
    for f in b.floors:
        if any(rm.type == "roof" for rm in f.rooms):
            continue
        g = Grid(f)
        ex = exits(f, g)
        dists = [g.dist(e) for e in ex]
        exit_cells = set().union(*ex) if ex else set()
        skip = {c for rm in f.rooms if rm.type in STAIRS | {"elevator"} for c in rm.cells}
        # 1. escape distance <= 35 m
        for c in f.footprint - skip - exit_cells:
            d = min((dd.get(c, math.inf) for dd in dists), default=math.inf)
            if d > max_escape:
                max_escape = d
                r["escape_where"] = (f.level, g.owner[c].type, len(ex))
        # 2. two escape routes
        for rm in f.rooms:
            if rm.type in STAIRS | {"elevator"}:
                continue
            c = next(iter(rm.cells))
            reach = sum(1 for dd in dists if c in dd)
            if reach < 2:
                two_strict = False
                sp = spec(rm.type)
                habitable = sp is not None and sp.windows.value == "required"
                has_window = any(
                    o.kind is OpeningKind.WINDOW
                    and any(x in rm.cells for e in o.edges for x in e.cells())
                    for o in f.openings
                )
                if habitable and not (has_window and 1 <= f.level <= 7):
                    two_lenient = False
        # 3. dead-end corridor <= 15 m
        corr = [rm for rm in f.rooms if rm.type == "corridor"]
        dij = [
            [
                min((dists[j].get(c, math.inf) for c in ex[i]), default=math.inf)
                for j in range(len(ex))
            ]
            for i in range(len(ex))
        ]
        for rm in corr:
            for c in rm.cells:
                ds = [dd.get(c, math.inf) for dd in dists]
                if len(ex) >= 2:
                    L = min(
                        (ds[i] + ds[j] - dij[i][j]) / 2
                        for i in range(len(ex))
                        for j in range(i + 1, len(ex))
                    )
                else:
                    L = ds[0] if ds else math.inf
                if max_dead < L:
                    max_dead = L
                    r["dead_where"] = (f.level, len(ex))
        # 4. corridor width
        for rm in corr:
            worst_corr = min(worst_corr, square_width(rm.cells))
        # 5./6. doors and turning circles
        blocked = {c for o in f.objects if o.blocking for c in o.cells}
        for o in f.openings:
            if o.kind is not OpeningKind.DOOR:
                continue
            doors_total += 1
            sides = [g.owner.get(c) for e in o.edges[:1] for c in e.cells()]
            if len(o.edges) < 2:
                doors_narrow += 1
                if not any(rm and spec(rm.type) and spec(rm.type).door_width == 1 for rm in sides):
                    doors_narrow_nonstall += 1
            for rm in sides:
                if rm is None:
                    continue
                turn_total += 1
                inner = [c for e in o.edges for c in e.cells() if c in rm.cells]
                if not any(free_square_at(c, rm.cells, blocked) for c in inner):
                    turn_fail += 1
                    small = not any(free_square_at(c, rm.cells, set()) for c in inner)
                    fails["turn"].append(
                        (rm.type, "room too small" if small else "furniture", len(o.edges))
                    )
        # 7. stairwell / elevator size
        for rm in f.rooms:
            if rm.type in STAIRS | {"elevator"}:
                x0, y0, x1, y1 = largest_rectangle(rm.cells)
                w, h = x1 - x0, y1 - y0
                a, z = sorted((w, h))
                if rm.type in STAIRS:
                    stair_dims.append((a, z))
                    if a < 5 or z < 9:
                        stairs_ok = False
                        fails["stair"].append((rm.type, a, z))
                elif a < 3:
                    elev_ok = False
        # 8. window area >= 1/8 floor area (window 1.5 m high)
        for rm in f.rooms:
            sp = spec(rm.type)
            if sp is None or sp.windows.value != "required":
                continue
            win_rooms += 1
            n = sum(
                len(o.edges)
                for o in f.openings
                if o.kind is OpeningKind.WINDOW
                and any(x in rm.cells for e in o.edges for x in e.cells())
            )
            if n == 0:
                win_none += 1
            if n * 0.5 * 1.5 < rm.area * 0.25 / 8:
                win_fail += 1
                fails["win"].append((rm.type, n, rm.area))
        # 9. ASR A1.2 workstation area
        desks = defaultdict(int)
        for o in f.objects:
            if o.kind in WORK_DESKS:
                desks[o.room] += 1
        for rm in f.rooms:
            n = desks.get(rm.id, 0)
            if n:
                desk_rooms += 1
                if rm.area * 0.25 < 8 + 6 * (n - 1):
                    desk_fail += 1
                    fails["desk"].append((rm.type, n, rm.area * 0.25))
        # 10. parking stalls and aisle
        for rm in f.rooms:
            if rm.type != "parking_deck":
                continue
            cars = [o for o in f.objects if o.room == rm.id and o.kind == "car"]
            others = {c for o in f.objects if o.blocking for c in o.cells}
            for car in cars:
                park_cars += 1
                along_x = car.facing in (Side.N, Side.S)
                # pitch: distance to the nearest car of the same row
                mates = [
                    o
                    for o in cars
                    if o is not car
                    and o.facing == car.facing
                    and ((o.y == car.y) if along_x else (o.x == car.x))
                ]
                pitch = 5
                if mates:
                    pitch = min(abs(o.x - car.x) if along_x else abs(o.y - car.y) for o in mates)
                    if pitch < 5:
                        park_pitch_fail += 1
                lane = 11 if pitch >= 5 else 13  # 5.5 m at 2.5 m stalls, else 6.5 m
                dx, dy = car.facing.delta
                front = [c for c in car.cells if Cell(c.x + dx, c.y + dy) not in car.cells]
                run = 0
                while run < lane:
                    nxt = [Cell(c.x + dx * (run + 1), c.y + dy * (run + 1)) for c in front]
                    if all(n in rm.cells and n not in others for n in nxt):
                        run += 1
                    else:
                        break
                if run < lane:
                    park_aisle_fail += 1
    r["fails"] = fails
    r.update(
        escape_m=round(max_escape * 0.5, 1),
        dead_end_m=round(max_dead * 0.5, 1),
        two_strict=two_strict,
        two_lenient=two_lenient,
        corridor_min_cells=worst_corr,
        doors=doors_total,
        doors_1cell=doors_narrow,
        doors_1cell_nonstall=doors_narrow_nonstall,
        turn_total=turn_total,
        turn_fail=turn_fail,
        stairs_ok=stairs_ok,
        stair_dims=sorted(set(stair_dims)),
        elev_ok=elev_ok,
        win_rooms=win_rooms,
        win_fail=win_fail,
        win_none=win_none,
        desk_rooms=desk_rooms,
        desk_fail=desk_fail,
        park_cars=park_cars,
        park_pitch_fail=park_pitch_fail,
        park_aisle_fail=park_aisle_fail,
    )
    return r


def run(cfg: dict) -> dict:
    b = build(cfg)
    if b is None:
        return {"type": cfg["building_type"], "failed": True}
    return check(b)


def summary(results: list[dict]) -> None:
    ok = [r for r in results if not r.get("failed")]
    failed = [r["type"] for r in results if r.get("failed")]

    def count(pred, among=ok) -> str:
        return f"{sum(1 for r in among if pred(r))} / {len(among)}"

    stairs = [r for r in ok if r["stair_dims"]]
    win = [r for r in ok if r["win_rooms"]]
    desk = [r for r in ok if r["desk_rooms"]]
    park = [r for r in ok if r["park_cars"]]
    rows = [
        ("escape distance <= 35 m", count(lambda r: r["escape_m"] <= 35)),
        ("two escape routes, strict", count(lambda r: r["two_strict"])),
        ("two escape routes, rescue windows", count(lambda r: r["two_lenient"])),
        ("dead-end corridor <= 15 m", count(lambda r: r["dead_end_m"] <= 15)),
        ("corridor >= 1.0 m", count(lambda r: r["corridor_min_cells"] >= 2)),
        ("corridor >= 1.5 m", count(lambda r: r["corridor_min_cells"] >= 3)),
        ("doors >= 0.9 m", count(lambda r: r["doors_1cell"] == 0)),
        ("doors >= 0.9 m except stalls", count(lambda r: r["doors_1cell_nonstall"] == 0)),
        ("turning space at every door", count(lambda r: r["turn_fail"] == 0)),
        ("stairwell 2.5 x 4.5 m", count(lambda r: r["stairs_ok"], stairs)),
        (
            "stairwell 2.5 x 4.0 m",
            count(lambda r: all(a >= 5 and z >= 8 for a, z in r["stair_dims"]), stairs),
        ),
        ("lift car 1.1 x 1.4 m", count(lambda r: r["elev_ok"])),
        ("window area >= 1/8", count(lambda r: r["win_fail"] == 0, win)),
        ("8 + 6 m2 per workstation", count(lambda r: r["desk_fail"] == 0, desk)),
        ("parking stall pitch", count(lambda r: r["park_pitch_fail"] == 0, park)),
        ("parking aisle", count(lambda r: r["park_aisle_fail"] == 0, park)),
    ]
    for name, value in rows:
        print(f"{name:36} {value}")
    print(
        f"{'door sides failing turning space':36} "
        f"{sum(r['turn_fail'] for r in ok)} / {sum(r['turn_total'] for r in ok)}"
    )
    print(
        f"{'rooms failing window area':36} "
        f"{sum(r['win_fail'] for r in ok)} / {sum(r['win_rooms'] for r in ok)}"
    )
    print(
        f"{'rooms failing workstation area':36} "
        f"{sum(r['desk_fail'] for r in ok)} / {sum(r['desk_rooms'] for r in ok)}"
    )
    if failed:
        print("not generated:", ", ".join(failed))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("count", type=int, nargs="?", default=100)
    parser.add_argument("--json", help="write per-building results (with failing rooms) here")
    args = parser.parse_args()
    with ProcessPoolExecutor() as pool:
        results = list(pool.map(run, configs(args.count)))
    if args.json:
        with open(args.json, "w") as out:
            json.dump(results, out, indent=1, default=str)
    summary(results)
