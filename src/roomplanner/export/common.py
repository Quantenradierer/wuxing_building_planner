"""Shared pieces of the VTT exports: grid scale, wall runs, door and window segments."""

from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass

from PIL import Image

from roomplanner.errors import RoomplannerError
from roomplanner.geometry import CELL_SIZE_M, Axis, Edge
from roomplanner.model import Building, Floor, Opening, OpeningKind
from roomplanner.render.image import DEFAULT_CELL_PX, RenderOptions, render_floor
from roomplanner.render.theme import Theme

type Point = tuple[float, float]
type Segment = tuple[Point, Point]


@dataclass(frozen=True)
class ExportOptions:
    grid_m: float = 1.0  # size of one VTT grid square in metres
    cell_px: int = DEFAULT_CELL_PX
    lights: bool = True
    baked_lighting: bool = False  # True: the image carries the light map, the VTT stays bright

    @property
    def cells_per_square(self) -> int:
        cells = self.grid_m / CELL_SIZE_M
        if cells < 1 or abs(cells - round(cells)) > 1e-9:
            raise RoomplannerError(
                f"grid size must be a multiple of {CELL_SIZE_M} m, got {self.grid_m}"
            )
        return round(cells)

    @property
    def render(self) -> RenderOptions:
        # Pad by one grid square so the VTT grid stays aligned with the cells.
        return RenderOptions(
            cell_px=self.cell_px,
            padding=self.cells_per_square,
            lighting=self.baked_lighting,
            supersample=1,  # same as the CLI image and the UI preview
        )

    @property
    def pixels_per_square(self) -> int:
        return self.cell_px * self.cells_per_square


def floor_image(
    building: Building, floor: Floor, theme: Theme, options: ExportOptions
) -> Image.Image:
    return render_floor(building, floor, theme, options.render)


def png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def png_base64(image: Image.Image) -> str:
    return base64.b64encode(png_bytes(image)).decode("ascii")


def runs(edges: set[Edge]) -> list[Segment]:
    """Maximal straight runs of edges as segments in cell coordinates."""
    segments: list[Segment] = []
    for axis in Axis:
        line = sorted(
            (e for e in edges if e.axis is axis),
            key=lambda e: (e.y, e.x) if axis is Axis.H else (e.x, e.y),
        )
        start: Edge | None = None
        previous: Edge | None = None
        for edge in [*line, None]:
            if edge is not None and previous is not None and previous.next_along() == edge:
                previous = edge
                continue
            if start is not None and previous is not None:
                segments.append(segment(start, previous))
            start = previous = edge
    return segments


def segment(first: Edge, last: Edge) -> Segment:
    """From the start vertex of `first` to the end vertex of `last`."""
    end = (last.x + 1, last.y) if last.axis is Axis.H else (last.x, last.y + 1)
    return (float(first.x), float(first.y)), (float(end[0]), float(end[1]))


def opening_segment(opening: Opening) -> Segment:
    return segment(opening.edges[0], opening.edges[-1])


def solid_walls(floor: Floor) -> set[Edge]:
    """Wall edges without any opening (doors, windows and breaches are exported apart)."""
    return set(floor.walls) - floor.cut_edges - {e for o in floor.openings for e in o.edges}


def wall_segments(floor: Floor) -> list[Segment]:
    """Solid walls as segments: straight runs and the floor's diagonals."""
    diagonals: list[Segment] = [
        ((float(a[0]), float(a[1])), (float(b[0]), float(b[1])))
        for a, b in (d.vertices() for d in sorted(floor.diagonals))
    ]
    return [*runs(solid_walls(floor)), *diagonals]


def openings_of(floor: Floor, kind: OpeningKind) -> list[Opening]:
    return [o for o in floor.openings if o.kind is kind]


def stable_id(*parts: object) -> str:
    """Deterministic 16-character id (Foundry document ids are 16 alphanumerics)."""
    digest = hashlib.sha256(":".join(map(str, parts)).encode()).digest()
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(alphabet[b % len(alphabet)] for b in digest[:16])
