"""How many office and storage rooms reach their chairs / shelves only along a 1-cell path?

    uv run python tools/pathwidth.py [count]

A target (chair; in storage rooms shelf) is "wide" when a free cell next to it can be reached
from a door through free cells that all lie in a free 2 x 2 block. Rooms with a target that is
not wide have a narrow path. Uses the codecheck sample of buildings.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

from codecheck import build, configs

from roomplanner.geometry import Cell, Edge, Side
from roomplanner.model import Building, OpeningKind
from roomplanner.pathways import served, wide_region

DESK_ROOMS = {"office", "back_office", "open_office", "manager_office", "executive_office"}
STORE_ROOMS = {"storage", "stockroom", "cold_storage"}
TARGET = {"chair": DESK_ROOMS, "shelf": STORE_ROOMS, "rack": STORE_ROOMS}


def _cells(o) -> list[Cell]:
    return [Cell(o.x + i, o.y + j) for i in range(o.w) for j in range(o.h)]


def check(b: Building) -> list[tuple[str, str, int, int]]:
    """(room type, id, targets, wide targets) for rooms that have targets."""
    out = []
    for f in b.floors:
        blocked = {c for o in f.objects if o.blocking for c in _cells(o)}
        for rm in f.rooms:
            kinds = {k for k, rts in TARGET.items() if rm.type in rts}
            if not kinds:
                continue
            targets = [o for o in f.objects if o.room == rm.id and o.kind in kinds]
            if not targets:
                continue
            free = {c for c in rm.cells if c not in blocked}
            starts = {
                c
                for o in f.openings
                if o.kind is OpeningKind.DOOR
                for e in o.edges
                for c in e.cells()
                if c in rm.cells
            } | {
                c
                for c in rm.cells
                for side in Side
                if (n := c.neighbour(side)) not in rm.cells
                and n in f.footprint
                and Edge.of(c, side) not in f.walls
            }
            region = wide_region(free, starts)
            n_wide = sum(served(_cells(o), free, region) for o in targets)
            out.append((rm.type, rm.id, len(targets), n_wide))
    return out


def run(cfg: dict):
    b = build(cfg)
    return check(b) if b else []


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    rooms = defaultdict(lambda: [0, 0, 0, 0])  # rooms, narrow rooms, targets, narrow targets
    with ProcessPoolExecutor() as ex:
        for res in ex.map(run, configs(n)):
            for t, _id, nt, nw in res:
                r = rooms[t]
                r[0] += 1
                r[1] += nw < nt
                r[2] += nt
                r[3] += nt - nw
    for t, (n_r, n_n, n_t, n_nt) in sorted(rooms.items()):
        print(
            f"{t:18} rooms {n_r:4}  narrow {n_n:4} ({n_n / n_r:4.0%})  "
            f"targets {n_t:5}  narrow {n_nt:5}"
        )
