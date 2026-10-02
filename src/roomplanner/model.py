"""Domain model. See docs/architecture.md, section "Data model"."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from roomplanner.geometry import Axis, Cell, Edge, Side
from roomplanner.params import GenerationParams, Wealth

SCHEMA_VERSION = 3
READABLE_SCHEMA_VERSIONS = (2, 3)  # version 3 only added fields and the `breach` kind


class OpeningKind(StrEnum):
    DOOR = "door"
    WINDOW = "window"
    BREACH = "breach"  # hole in a wall (condition layer); always passable


class OpeningState(StrEnum):
    INTACT = "intact"
    BROKEN = "broken"  # door: smashed, stuck open; window: shattered
    MISSING = "missing"  # door: no leaf left
    BLOCKED = "blocked"  # door: barricaded or welded shut, impassable


_STATES = {
    OpeningKind.DOOR: set(OpeningState),
    OpeningKind.WINDOW: {OpeningState.INTACT, OpeningState.BROKEN},
    OpeningKind.BREACH: {OpeningState.INTACT},
}


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
    state: OpeningState = OpeningState.INTACT
    # Doors only (all optional): what the leaf is made of, its lock and the lock's rating,
    # and for exterior doors which entrance they are (main, service, emergency).
    material: str | None = None
    lock: str | None = None
    rating: int | None = None
    entrance: str | None = None
    # Doors only: the leaves slide aside (elevator doors) instead of swinging; the swing
    # then only says which side of the wall they run on.
    sliding: bool = False

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
        elif self.sliding:
            raise ValueError(f"{self.kind} openings do not slide")
        if self.state not in _STATES[self.kind]:
            raise ValueError(f"a {self.kind} cannot be {self.state}")

    @property
    def axis(self) -> Axis:
        return self.edges[0].axis

    @property
    def passable(self) -> bool:
        """Whether people can walk through (doors unless blocked, breaches)."""
        if self.kind is OpeningKind.DOOR:
            return self.state is not OpeningState.BLOCKED
        return self.kind is OpeningKind.BREACH


@dataclass(frozen=True, slots=True)
class Room:
    id: str
    type: str
    cells: frozenset[Cell]
    unit: str | None = None  # e.g. the apartment the room belongs to

    @property
    def area(self) -> int:
        return len(self.cells)


@dataclass(frozen=True, slots=True)
class PlacedObject:
    """Furniture or fixture covering the cells [x, x+w) x [y, y+h) of one room."""

    kind: str
    x: int
    y: int
    w: int
    h: int
    facing: Side  # the side the object's front faces (away from the wall it stands at)
    room: str
    blocking: bool = True  # False: can be walked over (stairs, elevator car, rug)
    wealth: Wealth | None = None  # look (sprite variant); None: the building's wealth

    @property
    def cells(self) -> frozenset[Cell]:
        return frozenset(
            Cell(x, y)
            for x in range(self.x, self.x + self.w)
            for y in range(self.y, self.y + self.h)
        )


@dataclass(frozen=True, slots=True)
class Device:
    """Security device mounted in a cell: camera, alarm panel, motion sensor, …"""

    kind: str
    x: int
    y: int
    facing: Side  # cameras: view direction; wall devices: away from their wall
    room: str
    rating: int = 1


@dataclass(frozen=True, slots=True)
class Light:
    """Light source at a continuous position in cells (0, 0 = north-west map corner)."""

    kind: str  # ceiling, neon, emergency, flood, sign
    x: float
    y: float
    radius: float  # cells
    colour: str  # "#rrggbb"
    intensity: float = 1.0  # 0..1
    state: str = "on"  # on, flicker, off (broken or no power)
    room: str | None = None  # None: outside the building


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
    objects: tuple[PlacedObject, ...] = ()
    devices: tuple[Device, ...] = ()
    lights: tuple[Light, ...] = ()

    @property
    def name(self) -> str:
        return level_name(self.level)

    def room_at(self, cell: Cell) -> Room | None:
        for room in self.rooms:
            if cell in room.cells:
                return room
        return None

    def door_clearance(self, room: Room) -> frozenset[Cell]:
        """Cells of the room in front of its doors, as deep as the door is wide."""
        return self.door_clearances().get(room.id, frozenset())

    def door_clearances(self) -> dict[str, frozenset[Cell]]:
        """Door clearance of every room with a door, by room id: as deep as the door is wide
        on the side the leaf swings into, one step on the other (a hatch opening out)."""
        owner = {cell: room for room in self.rooms for cell in room.cells}
        clear: dict[str, set[Cell]] = {}
        for door in self.openings:
            if door.kind is not OpeningKind.DOOR:
                continue
            depth = len(door.edges)
            for edge in door.edges:
                a, b = edge.cells()
                for cell, other in ((a, b), (b, a)):
                    room = owner.get(cell)
                    if room is None:
                        continue
                    dx, dy = cell.x - other.x, cell.y - other.y
                    swept = door.swing is None or door.swing.towards.delta == (dx, dy)
                    for step in range(depth if swept else 1):
                        ahead = Cell(cell.x + dx * step, cell.y + dy * step)
                        if ahead in room.cells:
                            clear.setdefault(room.id, set()).add(ahead)
        return {room_id: frozenset(cells) for room_id, cells in clear.items()}

    def passable_edges(self) -> frozenset[Edge]:
        """Wall edges that can be walked through (open doors, breaches)."""
        return frozenset(e for o in self.openings if o.passable for e in o.edges)

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
