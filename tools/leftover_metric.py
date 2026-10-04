"""How much of a floor ends as leftover, per layout strategy (partition vs corridor).

    uv run python tools/leftover_metric.py [--type office] [--seeds 8] [layout ...]

Leftover is every cell that no room was planned for: space handed to a storeroom filler or
flagged as leftover and merged into a neighbour afterwards (`absorb_leftovers`), side hallway
ends joined to rooms (`trim_hallways`), and the cells of storage-like rooms (storage, closets,
supply rooms, the program's cluster filler) in the finished plan. Only the layout stage runs,
on upper floors that are not roofs, so the numbers compare the layouts and nothing else.
"""

from __future__ import annotations

import argparse
import dataclasses
import random
from collections.abc import Callable
from typing import Any

import roomplanner.pipeline  # noqa: F401  registers the strategies
from roomplanner.errors import InfeasibleError
from roomplanner.params import BuildingType, GenerationParams
from roomplanner.pipeline.base import AllocationError, Context, PlannedRoom
from roomplanner.pipeline.layout import corridor as corridor_module
from roomplanner.pipeline.layout import partition as partition_module
from roomplanner.pipeline.registry import resolve
from roomplanner.rules import rules_for

STORAGE_WORDS = ("storage", "closet", "supply", "stockroom")
SIZES = [(36, 24), (48, 30), (60, 40), (80, 50), (90, 60)]


class Counter:
    """Cells that went through the merge steps of the layouts."""

    absorbed = 0
    trimmed = 0


def _wrap_absorb(original: Callable[..., list[PlannedRoom]]) -> Callable[..., list[PlannedRoom]]:
    def wrapped(rooms: list[PlannedRoom], *args: Any, **kwargs: Any) -> list[PlannedRoom]:
        result = original(rooms, *args, **kwargs)
        Counter.absorbed += sum(len(r.cells) for r in rooms if r.leftover) - sum(
            len(r.cells) for r in result if r.leftover
        )
        return result

    return wrapped


def _wrap_trim(original: Callable[..., list[PlannedRoom]]) -> Callable[..., list[PlannedRoom]]:
    def wrapped(rooms: list[PlannedRoom], *args: Any, **kwargs: Any) -> list[PlannedRoom]:
        result = original(rooms, *args, **kwargs)
        Counter.trimmed += sum(len(r.cells) for r in rooms if r.hallway) - sum(
            len(r.cells) for r in result if r.hallway
        )
        return result

    return wrapped


def measure(layout: str, kind: BuildingType, seeds: int) -> tuple[float, float, int, int]:
    """(leftover share, corridor share, floors measured, failures) over the size matrix."""
    leftover = corridor = total = floors = failed = 0
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
            Counter.absorbed = Counter.trimmed = 0
            try:
                if strategy.check_feasibility(ctx, footprint):
                    continue
                plan = strategy.layout(ctx, footprint)
            except (AllocationError, InfeasibleError) as error:
                failed += 1
                print(f"  failed {width}x{depth} seed {seed}: {error}")
                continue
            floor = plan.floors[1]
            n = sum(len(r.cells) for r in floor.rooms)
            filler = {rules.program.cluster_filler}
            kept = sum(
                len(r.cells)
                for r in floor.rooms
                if r.leftover or r.type in filler or any(w in r.type for w in STORAGE_WORDS)
            )
            # merge counters cover all floors of the plan: scale to the measured floor
            merged = (Counter.absorbed + Counter.trimmed) / len(plan.floors)
            leftover += kept + merged
            corridor += sum(len(r.cells) for r in floor.rooms if r.type == "corridor")
            total += n
            floors += 1
    return leftover / max(1, total), corridor / max(1, total), floors, failed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", default="office")
    parser.add_argument("--seeds", type=int, default=6)
    parser.add_argument("layouts", nargs="*", default=["corridor", "partition"])
    args = parser.parse_args()
    corridor_module.absorb_leftovers = _wrap_absorb(corridor_module.absorb_leftovers)  # type: ignore[attr-defined]
    corridor_module.trim_hallways = _wrap_trim(corridor_module.trim_hallways)  # type: ignore[attr-defined]
    partition_module.absorb_leftovers = _wrap_absorb(partition_module.absorb_leftovers)  # type: ignore[attr-defined]
    for layout in args.layouts:
        share, corridors, floors, failed = measure(layout, BuildingType(args.type), args.seeds)
        print(
            f"{layout:10} leftover {share:6.1%}  corridor {corridors:6.1%}  "
            f"floors {floors}  failed {failed}"
        )


if __name__ == "__main__":
    main()
