"""Domain model. See docs/architecture.md, section "Data model"."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from roomplanner.geometry import Axis, Cell, Edge, Side
from roomplanner.params import GenerationParams

SCHEMA_VERSION = 2


class OpeningKind(StrEnum):
    DOOR = "door"
    WINDOW = "window"


@dataclass(frozen=True, slots=True)
class Swing:
    """How a door leaf moves: into which side of its wall, hinged at which end of the run."""

    towards: Side
    hinge: Side


@dataclass(frozen=True, slots=True)
class Opening:
    kind: OpeningKind
    edges: tuple[Edge, ...]
    swing: Swing | None = None

    def __post_init__(self) -> None:
        if not self.edges:
            raise ValueError("opening needs at least one edge")
        if list(self.edges) != sorted(self.edges):
            raise ValueError("opening edges must be sorted")
        for current, following in zip(self.edges, self.edges[1:], strict=False):
            if current.next_along() != following:
                raise ValueError("opening edges must be a straight, contiguous run")
        if self.kind is OpeningKind.DOOR:
            if self.swing is None:
                raise ValueError("doors need a swing")
            # Side.axis is the axis of edges on that side: a horizontal wall faces N/S
            # and its run ends are W/E.
            if self.swing.towards.axis is not self.axis:
                raise ValueError("a door must swing perpendicular to its wall")
            if self.swing.hinge.axis is self.axis:
                raise ValueError("a door hinge must sit at one end of its wall run")
        elif self.swing is not None:
            raise ValueError(f"{self.kind} openings have no swing")

    @property
    def axis(self) -> Axis:
        return self.edges[0].axis


@dataclass(frozen=True, slots=True)
class Room:
    id: str
    type: str
    cells: frozenset[Cell]
    unit: str | None = None  # e.g. the apartment the room belongs to

    @property
    def area(self) -> int:
        return len(self.cells)


def level_name(level: int) -> str:
    if level == 0:
        return "Ground floor"
    if level > 0:
        return f"Floor {level}"
    return f"Basement B{-level}"


@dataclass(frozen=True, slots=True)
class Floor:
    level: int
    footprint: frozenset[Cell]
    rooms: tuple[Room, ...]
    walls: frozenset[Edge]
    openings: tuple[Opening, ...] = ()
    role: str = ""

    @property
    def name(self) -> str:
        return level_name(self.level)

    def room_at(self, cell: Cell) -> Room | None:
        for room in self.rooms:
            if cell in room.cells:
                return room
        return None

    def is_exterior_wall(self, edge: Edge) -> bool:
        a, b = edge.cells()
        return (a in self.footprint) != (b in self.footprint)


@dataclass(frozen=True, slots=True)
class Building:
    params: GenerationParams
    seed: int
    width: int
    height: int
    floors: tuple[Floor, ...]
    warnings: tuple[str, ...] = field(default=())
    schema_version: int = SCHEMA_VERSION

    def floor(self, level: int) -> Floor:
        for floor in self.floors:
            if floor.level == level:
                return floor
        raise KeyError(f"no floor at level {level}")
