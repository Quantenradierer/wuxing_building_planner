"""Building generation entry point.

Milestone 1: every floor is a single placeholder room with exterior doors and windows.
The staged pipeline (docs/architecture.md, "Pipeline") replaces this in milestone 2.
"""

from __future__ import annotations

import random
import secrets

from roomplanner.errors import InfeasibleError, NotSupportedError
from roomplanner.geometry import (
    CELL_SIZE_M,
    Axis,
    Edge,
    Side,
    boundary_edges,
    meters_to_cells,
    rectangle,
)
from roomplanner.model import Building, Floor, Opening, OpeningKind, Room, Swing
from roomplanner.params import GenerationParams, Shape

MIN_SIDE_CELLS = 4
DOOR_WIDTH = 2
WINDOW_WIDTH = 3
WINDOW_PITCH = 8
CORNER_MARGIN = 1


def stage_rng(seed: int, stage: str) -> random.Random:
    """Independent, reproducible random stream per pipeline stage."""
    return random.Random(f"{seed}:{stage}")


def generate(params: GenerationParams) -> Building:
    if params.shape is not Shape.RECTANGLE:
        raise NotSupportedError(f"shape '{params.shape}' is not implemented yet")

    width = meters_to_cells(params.width_m)
    height = meters_to_cells(params.depth_m)
    if min(width, height) < MIN_SIDE_CELLS:
        raise InfeasibleError(
            f"building must be at least {MIN_SIDE_CELLS * CELL_SIZE_M:g} m on each side, "
            f"got {params.width_m:g} m x {params.depth_m:g} m"
        )

    seed = params.seed if params.seed is not None else secrets.randbelow(2**32)
    params = params.model_copy(update={"seed": seed})

    footprint = rectangle(0, 0, width, height)
    walls = boundary_edges(footprint)
    doors = _exterior_doors(params, width, height, stage_rng(seed, "entrances"))
    door_edges = {e for d in doors for e in d.edges}
    windows = _windows(width, height, door_edges, stage_rng(seed, "windows"))

    floors: list[Floor] = []
    for level in params.levels:
        openings: list[Opening] = []
        if level == 0:
            openings += doors
        if level >= 0:
            openings += windows
        room = Room(id=f"{level}.1", type="placeholder", cells=footprint)
        floors.append(Floor(level, footprint, (room,), walls, tuple(openings)))

    return Building(params=params, seed=seed, width=width, height=height, floors=tuple(floors))


def side_edges(width: int, height: int, side: Side) -> list[Edge]:
    """Exterior edges of a width x height rectangle on one side, west-to-east / north-to-south."""
    match side:
        case Side.N:
            return [Edge(x, 0, Axis.H) for x in range(width)]
        case Side.S:
            return [Edge(x, height, Axis.H) for x in range(width)]
        case Side.W:
            return [Edge(0, y, Axis.V) for y in range(height)]
        case Side.E:
            return [Edge(width, y, Axis.V) for y in range(height)]


def _exterior_doors(
    params: GenerationParams, width: int, height: int, rng: random.Random
) -> list[Opening]:
    sides = [params.street_side]
    if params.service_side is not params.street_side:
        sides.append(params.service_side)
    return [_exit_door(side_edges(width, height, side), side, rng) for side in sides]


def _exit_door(edges: list[Edge], side: Side, rng: random.Random) -> Opening:
    """A door roughly centered on a wall run, opening outwards."""
    centre = (len(edges) - DOOR_WIDTH) // 2
    jitter = max(0, (len(edges) - DOOR_WIDTH) // 4)
    start = min(max(centre + rng.randint(-jitter, jitter), 0), len(edges) - DOOR_WIDTH)
    hinges = (Side.W, Side.E) if side.axis is Axis.H else (Side.N, Side.S)
    return Opening(
        OpeningKind.DOOR,
        tuple(edges[start : start + DOOR_WIDTH]),
        Swing(towards=side, hinge=rng.choice(hinges)),
    )


def _windows(width: int, height: int, blocked: set[Edge], rng: random.Random) -> list[Opening]:
    windows: list[Opening] = []
    for side in Side:
        edges = side_edges(width, height, side)
        position = CORNER_MARGIN + rng.randrange(WINDOW_PITCH - WINDOW_WIDTH)
        while position + WINDOW_WIDTH <= len(edges) - CORNER_MARGIN:
            run = edges[position : position + WINDOW_WIDTH]
            # Keep one edge of solid wall between windows and doors.
            margin = edges[max(position - 1, 0) : position + WINDOW_WIDTH + 1]
            if blocked.isdisjoint(margin):
                windows.append(Opening(OpeningKind.WINDOW, tuple(run)))
            position += WINDOW_PITCH
    return windows
