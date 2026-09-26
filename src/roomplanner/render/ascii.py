"""Minimal ASCII debug renderer on a doubled grid. See docs/architecture.md."""

from __future__ import annotations

import string

from roomplanner.geometry import Axis, Cell, Edge
from roomplanner.model import Building, Floor, Opening, OpeningKind, OpeningState, Room
from roomplanner.rules import load_objects


def render_building(building: Building) -> str:
    p = building.params
    header = (
        f"{p.building_type} {building.width}x{building.height} cells, "
        f"wealth={p.wealth}, seed={building.seed}"
    )
    parts = [header]
    glyphs = {name: spec.glyph for name, spec in load_objects().items()}
    parts += [
        render_floor(f, building.width, building.height, glyphs) for f in reversed(building.floors)
    ]
    if building.warnings:
        parts.append("Warnings:\n" + "\n".join(f"  - {w}" for w in building.warnings))
    return "\n\n".join(parts) + "\n"


def render_floor(
    floor: Floor, width: int, height: int, glyphs: dict[str, str] | None = None
) -> str:
    canvas = [[" "] * (2 * width + 1) for _ in range(2 * height + 1)]
    glyphs = glyphs or {}

    for edge in floor.walls:
        row, col = _edge_pos(edge)
        canvas[row][col] = "-" if edge.axis is Axis.H else "|"
    for opening in floor.openings:
        for edge in opening.edges:
            row, col = _edge_pos(edge)
            canvas[row][col] = _opening_glyph(opening, edge.axis)
    for row in range(0, 2 * height + 1, 2):
        for col in range(0, 2 * width + 1, 2):
            canvas[row][col] = _vertex_glyph(canvas, row, col)

    kinds = sorted({o.kind for o in floor.objects})
    floor_glyphs = _unique_glyphs(kinds, glyphs)
    # Objects fill their cells and the grid positions between them, so they read as blocks.
    for obj in floor.objects:
        glyph = floor_glyphs[obj.kind]
        for row in range(2 * obj.y + 1, 2 * (obj.y + obj.h)):
            for col in range(2 * obj.x + 1, 2 * (obj.x + obj.w)):
                canvas[row][col] = glyph

    legend: list[str] = []
    if kinds:
        legend.append("  objects: " + ", ".join(f"{floor_glyphs[k]} {k}" for k in kinds))
    for number, room in enumerate(floor.rooms, start=1):
        _place_label(canvas, room, str(number))
        unit = f"  [{room.unit}]" if room.unit else ""
        legend.append(f"  {number:>3}  {room.type:<16} {room.area:6d} cells{unit}")

    lines = ["".join(row).rstrip() for row in canvas]
    return "\n".join([f"== {floor.name} ==", *lines, *legend])


# Walls, openings and room numbers; objects never use these, so every glyph means one thing.
RESERVED_GLYPHS = frozenset(' -|+DOX/:=%"?' + string.digits)
_SPARE = [c for c in string.ascii_letters + string.punctuation if c not in RESERVED_GLYPHS]


def _unique_glyphs(kinds: list[str], preferred: dict[str, str]) -> dict[str, str]:
    """One distinct glyph per object kind of a floor.

    The catalog glyph wins if it is free, else the kind's initial (lower, then upper case),
    else the first spare character; '?' only once all are taken.
    """
    result: dict[str, str] = {}
    used: set[str] = set()
    pending: list[str] = []
    for kind in kinds:
        glyph = preferred.get(kind, "")
        if glyph and glyph not in RESERVED_GLYPHS and glyph not in used:
            result[kind] = glyph
            used.add(glyph)
        else:
            pending.append(kind)
    for kind in pending:
        candidates = [kind[0].lower(), kind[0].upper(), *_SPARE]
        glyph = next((c for c in candidates if c not in RESERVED_GLYPHS and c not in used), "?")
        result[kind] = glyph
        used.add(glyph)
    return result


_DOOR_GLYPHS = {
    OpeningState.INTACT: "D",
    OpeningState.BROKEN: "/",
    OpeningState.MISSING: "O",
    OpeningState.BLOCKED: "X",
}


def _opening_glyph(opening: Opening, axis: Axis) -> str:
    match opening.kind:
        case OpeningKind.DOOR:
            return _DOOR_GLYPHS[opening.state]
        case OpeningKind.BREACH:
            return "%"
        case OpeningKind.WINDOW:
            if opening.state is OpeningState.BROKEN:
                return ":"
            return "=" if axis is Axis.H else '"'


def _vertex_glyph(canvas: list[list[str]], row: int, col: int) -> str:
    """'+' where walls meet at an angle, otherwise continue a straight run."""

    def at(r: int, c: int) -> str:
        inside = 0 <= r < len(canvas) and 0 <= c < len(canvas[0])
        return canvas[r][c] if inside else " "

    left, right, up, down = at(row, col - 1), at(row, col + 1), at(row - 1, col), at(row + 1, col)
    horizontal = left != " " or right != " "
    vertical = up != " " or down != " "
    if horizontal and vertical:
        return "+"
    if horizontal:
        return left if left == right else "-"
    if vertical:
        return up if up == down else "|"
    return " "


def _edge_pos(edge: Edge) -> tuple[int, int]:
    """(row, col) of an edge on the doubled grid."""
    if edge.axis is Axis.H:
        return 2 * edge.y, 2 * edge.x + 1
    return 2 * edge.y + 1, 2 * edge.x


def _place_label(canvas: list[list[str]], room: Room, label: str) -> None:
    """Write the label left-to-right starting at the free room cell nearest the centroid."""
    cx = sum(c.x for c in room.cells) / len(room.cells)
    cy = sum(c.y for c in room.cells) / len(room.cells)

    def distance(cell: Cell) -> float:
        return (cell.x - cx) ** 2 + (cell.y - cy) ** 2

    for cell in sorted(room.cells, key=lambda c: (distance(c), c)):
        row, col = 2 * cell.y + 1, 2 * cell.x + 1
        if col + len(label) <= len(canvas[row]) and all(
            canvas[row][col + i] == " " for i in range(len(label))
        ):
            canvas[row][col : col + len(label)] = list(label)
            return
