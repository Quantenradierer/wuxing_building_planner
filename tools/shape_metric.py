"""Narrow and irregular rooms of the partition layouts.

    uv run python tools/shape_metric.py [--type office] [--seeds 6] [--worst 12] [layout ...]

Per layout, over the size matrix of `leftover_metric.py` (layout stage only, floor 1): the
share of rooms with more than `MAX_CORNERS` corners ("odd") and of rooms whose thinnest
straight extent is below `NARROW` cells ("narrow"). Corridors, hallways and hubs are
circulation and not counted.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import random
from typing import Any

import roomplanner.pipeline  # noqa: F401  registers the strategies
from roomplanner.errors import InfeasibleError
from roomplanner.generator import generate
from roomplanner.geometry import corners, thinnest_extent
from roomplanner.params import BuildingType, GenerationParams
from roomplanner.pipeline.base import AllocationError, Context
from roomplanner.pipeline.registry import resolve
from roomplanner.rules import rules_for

MAX_CORNERS = 6
NARROW = 3
SIZES = [(36, 24), (48, 30), (60, 40), (80, 50), (90, 60)]


def measure(layout: str, kind: BuildingType, seeds: int, worst: int) -> None:
    rooms = odd = narrow = floors = failed = 0
    by_type: collections.Counter[str] = collections.Counter()
    examples: list[tuple[int, str, str, int, int, bool]] = []
    for width, depth in SIZES:
        for seed in range(seeds):
            rng = random.Random(f"{width}:{depth}:{seed}")
            params = GenerationParams(
                building_type=kind,
                width=width,
                depth=depth,
                floors_above=2,
                seed=seed,
                street_side=rng.choice(
                    list(GenerationParams.model_fields["street_side"].annotation)
                ),  # type: ignore[arg-type]
            )
            rules = rules_for(params.building_type, params.wealth, params.security)
            rules = dataclasses.replace(
                rules, program=rules.program.model_copy(update={"layout": layout})
            )
            ctx = Context(params, rules, seed, width, depth, 0)
            strategy: Any = resolve("layout", layout)
            footprint = resolve("footprint", "rectangle").footprint(ctx)
            try:
                if strategy.check_feasibility(ctx, footprint):
                    continue
                plan = strategy.layout(ctx, footprint)
            except (AllocationError, InfeasibleError) as error:
                failed += 1
                print(f"  failed {width}x{depth} seed {seed}: {error}")
                continue
            floors += 1
            for room in plan.floors[1].rooms:
                if room.type in ("corridor", "stall") or room.hallway or room.hub:
                    continue
                rooms += 1
                n = corners(room.cells)
                thin = thinnest_extent(room.cells, room.cells)
                if n > MAX_CORNERS:
                    odd += 1
                    by_type[room.type + " odd"] += 1
                if thin < NARROW:
                    narrow += 1
                    by_type[room.type + " narrow"] += 1
                if n > MAX_CORNERS or thin < NARROW:
                    examples.append(
                        (
                            n,
                            room.type,
                            f"{width}x{depth}s{seed}",
                            thin,
                            len(room.cells),
                            room.leftover,
                        )
                    )
    print(
        f"{layout:20} rooms {rooms}  odd {odd / max(1, rooms):6.1%}  "
        f"narrow {narrow / max(1, rooms):6.1%}  floors {floors}  failed {failed}"
    )
    for name, count in by_type.most_common(worst):
        print(f"    {count:4}  {name}")
    for n, rtype, where, thin, size, left in sorted(examples, reverse=True)[:worst]:
        note = " leftover" if left else ""
        print(f"    corners {n:2} thin {thin} cells {size:3} {rtype} {where}{note}")


def hard(layout: str, kind: BuildingType, seeds: int) -> None:
    """Hard violations of complete buildings over the size matrix."""
    total = buildings = 0
    for width, depth in [*SIZES, (104, 103), (120, 115)]:
        for seed in range(seeds):
            params = GenerationParams(
                building_type=kind, width=width, depth=depth, floors_above=1, seed=seed,
                layout=layout,
            )  # fmt: skip
            try:
                building = generate(params)
            except AllocationError, InfeasibleError:
                continue
            buildings += 1
            found = [w for w in building.warnings if w.startswith("[hard]")]
            total += len(found)
            for warning in found:
                print(f"  {width}x{depth} seed {seed}: {warning}")
    print(f"{layout:20} hard violations {total} in {buildings} buildings")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", default="office")
    parser.add_argument("--seeds", type=int, default=6)
    parser.add_argument("--worst", type=int, default=12)
    parser.add_argument("--hard", action="store_true", help="count hard violations instead")
    parser.add_argument("layouts", nargs="*", default=["partition", "partition_diagonal"])
    args = parser.parse_args()
    for layout in args.layouts:
        if args.hard:
            hard(layout, BuildingType(args.type), args.seeds)
            continue
        measure(layout, BuildingType(args.type), args.seeds, args.worst)


if __name__ == "__main__":
    main()
