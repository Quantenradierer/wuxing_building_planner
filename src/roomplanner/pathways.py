"""Two-cell pathways: can a room's chairs (or shelves) be reached along a path 2 cells wide?

A cell is *wide* when it lies in a 2 x 2 block of free cells. The wide region is the wide cells
connected to the room's entries (door cells, open sides). A target is served when a free cell
next to it is wide and in that region, or touches it (a chair in a one-cell niche that opens
onto the aisle).
"""

from __future__ import annotations

from collections.abc import Iterable

from roomplanner.geometry import Cell, Side

SIDES = list(Side)


def wide_cells(free: set[Cell] | frozenset[Cell]) -> set[Cell]:
    """The free cells that lie in a 2 x 2 block of free cells."""
    out: set[Cell] = set()
    for c in free:
        if c in out:
            continue
        if (
            Cell(c.x + 1, c.y) in free
            and Cell(c.x, c.y + 1) in free
            and Cell(c.x + 1, c.y + 1) in free
        ):
            out.update((c, Cell(c.x + 1, c.y), Cell(c.x, c.y + 1), Cell(c.x + 1, c.y + 1)))
    return out


def wide_region(free: set[Cell] | frozenset[Cell], entries: Iterable[Cell]) -> set[Cell]:
    """Wide cells connected to the entries (an entry cell, or a cell next to it)."""
    wide = wide_cells(free)
    near: set[Cell] = set()
    for e in entries:
        near.add(e)
        near.update(e.neighbour(s) for s in SIDES)
    seen = wide & near
    stack = list(seen)
    while stack:
        c = stack.pop()
        for s in SIDES:
            n = c.neighbour(s)
            if n in wide and n not in seen:
                seen.add(n)
                stack.append(n)
    return seen


def served(target: Iterable[Cell], free: set[Cell] | frozenset[Cell], region: set[Cell]) -> bool:
    """True if a free cell next to the target is in the wide region or touches it."""
    own = set(target)
    for c in own:
        for s in SIDES:
            n = c.neighbour(s)
            if n in own or n not in free:
                continue
            if n in region or any(n.neighbour(t) in region for t in SIDES):
                return True
    return False
