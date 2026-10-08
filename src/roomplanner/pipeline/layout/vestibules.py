"""Vestibules in partition layouts: a small room (a mantrap) carved from a corner of a room
where it touches circulation; the room is then entered only through it.

Only rooms whose catalog entry has `corner_vestibule` and `vestibule_chance` (by security level)
get one, and only if the rest of the room stays big enough and in one piece.
"""

from __future__ import annotations

import random

from roomplanner.geometry import Cell, Side, connected, thinnest_extent
from roomplanner.params import Security
from roomplanner.pipeline.base import PlannedRoom
from roomplanner.rules import Rules


def carve_vestibules(
    rooms: list[PlannedRoom],
    rules: Rules,
    circulation: frozenset[Cell],
    security: Security,
    rng: random.Random,
) -> list[PlannedRoom]:
    result: list[PlannedRoom] = []
    for room in rooms:
        result.append(room)
        spec = rules.spec(room.type)
        if (
            spec.corner_vestibule is None
            or room.host is not None
            or rng.random() >= spec.vestibule_chance.get(security, 0.0)
        ):
            continue
        vest = rules.spec(spec.corner_vestibule)
        width = vest.min_side
        depth = width + 1  # 1.5 x 2 m: one person, two doors
        cells = _corner(room.cells, circulation, width, depth, spec.area[0], spec.min_side, rng)
        if cells is None:
            continue
        vestibule = PlannedRoom(spec.corner_vestibule, cells)
        host = PlannedRoom(room.type, room.cells - cells, host=vestibule, front=room.front)
        result[-1] = host
        result.append(vestibule)
    return result


def _corner(
    cells: frozenset[Cell],
    circulation: frozenset[Cell],
    width: int,
    depth: int,
    keep: int,
    thin: int,
    rng: random.Random,
) -> frozenset[Cell] | None:
    """A width x depth block of `cells` with its `width` side along circulation."""
    found: list[frozenset[Cell]] = []
    for cell in sorted(cells):
        for side in Side:
            if cell.neighbour(side) not in circulation:
                continue
            ax, ay = (1, 0) if side in (Side.N, Side.S) else (0, 1)
            ix, iy = -side.delta[0], -side.delta[1]  # inward
            for start in (0, -(width - 1)):
                block = frozenset(
                    Cell(
                        cell.x + (start + i) * ax + j * ix,
                        cell.y + (start + i) * ay + j * iy,
                    )
                    for i in range(width)
                    for j in range(depth)
                )
                rest = cells - block
                if (
                    block <= cells
                    and len(rest) >= keep
                    and connected(rest)
                    and thinnest_extent(rest, rest, thin) >= thin
                    # the whole width touches circulation, the block sits in a corner
                    and sum(c.neighbour(side) in circulation for c in block) >= width
                    and _in_corner(block, cells, side)
                ):
                    found.append(block)
    if not found:
        return None
    return rng.choice(sorted(found, key=min))


def _in_corner(block: frozenset[Cell], cells: frozenset[Cell], side: Side) -> bool:
    """At least one flank of the block is the room's edge (not the middle of a wall)."""
    ax, ay = (1, 0) if side in (Side.N, Side.S) else (0, 1)
    flanks = [(ax, ay), (-ax, -ay)]
    return any(
        all(
            Cell(c.x + dx, c.y + dy) in block or Cell(c.x + dx, c.y + dy) not in cells
            for c in block
        )
        for dx, dy in flanks
    )
