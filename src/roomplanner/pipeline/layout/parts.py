"""Split a footprint made of rectangles into a main part plus a tree of wings.

The footprint is cut along every x and y where its outline changes, giving a coarse grid of
blocks. The main part is a maximal rectangle of blocks; wings are rectangles of the
remaining blocks, each attached with one whole end to an already chosen part (its parent),
so the wing's corridors can start at the junction. Among all main parts the decomposition
that keeps a facade on the street, fits the minimum depths and has the biggest main part
wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise, product

from roomplanner.geometry import Cell, Side
from roomplanner.pipeline.layout.frame import Box, Frame


@dataclass(frozen=True)
class Piece:
    box: Box
    parent: int | None = None  # index of the part this wing is attached to
    junction: Side | None = None  # side of the parent the wing is attached to


def wing_frame(wing: Box, junction: Side) -> Frame:
    """Frame whose u runs away from the parent, u = 0 at the junction."""
    width, height = wing.x1 - wing.x0, wing.y1 - wing.y0
    match junction:
        case Side.N:
            return Frame(False, height, width, wing.x0, wing.y0, flip_u=True)
        case Side.S:
            return Frame(False, height, width, wing.x0, wing.y0)
        case Side.W:
            return Frame(True, width, height, wing.x0, wing.y0, flip_u=True)
        case Side.E:
            return Frame(True, width, height, wing.x0, wing.y0)


def depth_of(piece: Piece) -> int:
    if piece.junction is None:
        return Frame.for_rect(piece.box).depth
    return wing_frame(piece.box, piece.junction).depth


def decompose(
    footprint: frozenset[Cell], street: Side, main_depth: int, wing_depth: int
) -> list[Piece]:
    """Main part first, then wings in attachment order (parents before children)."""
    xs, ys = _cuts(footprint, 0), _cuts(footprint, 1)
    blocks = {
        (i, j)
        for i, j in product(range(len(xs) - 1), range(len(ys) - 1))
        if Cell(xs[i], ys[j]) in footprint
    }
    rects = [r for r in _rects(len(xs) - 1, len(ys) - 1) if _cells(r) <= blocks]
    bbox = Box(xs[0], ys[0], xs[-1], ys[-1])

    def box(r: tuple[int, int, int, int]) -> Box:
        return Box(xs[r[0]], ys[r[1]], xs[r[2]], ys[r[3]])

    best: tuple[tuple[bool, bool, int, int], list[Piece]] | None = None
    for main in _maximal(rects):
        pieces = [Piece(box(main))]
        remaining = blocks - _cells(main)
        while remaining:
            options: list[tuple[tuple[bool, int], Piece, tuple[int, int, int, int]]] = []
            for r in rects:
                if not _cells(r) <= remaining:
                    continue
                attached = _attach(box(r), pieces)
                if attached is not None:
                    fits = depth_of(attached) >= wing_depth
                    options.append(((fits, _area(box(r))), attached, r))
            if not options:
                break
            _, piece, r = max(options, key=lambda o: o[0])
            pieces.append(piece)
            remaining -= _cells(r)
        if remaining:
            continue
        fits = depth_of(pieces[0]) >= main_depth and all(
            depth_of(p) >= wing_depth for p in pieces[1:]
        )
        score = (_touches(pieces[0].box, bbox, street), fits, _area(pieces[0].box), -len(pieces))
        if best is None or score > best[0]:
            best = (score, pieces)
    assert best is not None, "a footprint always decomposes into rectangles"
    return best[1]


def _cuts(footprint: frozenset[Cell], axis: int) -> list[int]:
    """Coordinates along one axis where the footprint's cross-section changes."""
    lines: dict[int, set[int]] = {}
    for cell in footprint:
        lines.setdefault(cell[axis], set()).add(cell[1 - axis])
    keys = sorted(lines)
    cuts = [keys[0]]
    for previous, current in pairwise(keys):
        if lines[previous] != lines[current]:
            cuts.append(current)
    return [*cuts, keys[-1] + 1]


def _rects(nx: int, ny: int) -> list[tuple[int, int, int, int]]:
    return [
        (i0, j0, i1, j1)
        for i0 in range(nx)
        for i1 in range(i0 + 1, nx + 1)
        for j0 in range(ny)
        for j1 in range(j0 + 1, ny + 1)
    ]


def _cells(r: tuple[int, int, int, int]) -> set[tuple[int, int]]:
    return set(product(range(r[0], r[2]), range(r[1], r[3])))


def _maximal(rects: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    return [r for r in rects if not any(o != r and _cells(r) <= _cells(o) for o in rects)]


def _area(box: Box) -> int:
    return (box.x1 - box.x0) * (box.y1 - box.y0)


def _touches(box: Box, bbox: Box, side: Side) -> bool:
    match side:
        case Side.N:
            return box.y0 == bbox.y0
        case Side.S:
            return box.y1 == bbox.y1
        case Side.W:
            return box.x0 == bbox.x0
        case Side.E:
            return box.x1 == bbox.x1


def _attach(wing: Box, pieces: list[Piece]) -> Piece | None:
    """The wing attached with one whole side to one part, or None."""
    for index, piece in enumerate(pieces):
        p = piece.box
        spans_x = p.x0 <= wing.x0 and wing.x1 <= p.x1
        spans_y = p.y0 <= wing.y0 and wing.y1 <= p.y1
        if wing.y1 == p.y0 and spans_x:
            return Piece(wing, index, Side.N)
        if wing.y0 == p.y1 and spans_x:
            return Piece(wing, index, Side.S)
        if wing.x1 == p.x0 and spans_y:
            return Piece(wing, index, Side.W)
        if wing.x0 == p.x1 and spans_y:
            return Piece(wing, index, Side.E)
    return None
