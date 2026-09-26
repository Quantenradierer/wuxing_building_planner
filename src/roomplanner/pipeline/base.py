"""Pipeline contracts: the context, the intermediate plan and the stage protocols.

Stages communicate only through these types and the model. See docs/decisions/0003.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Protocol

from roomplanner.geometry import CELL_AREA_M2, Cell, Side, meters_to_cells
from roomplanner.model import Floor
from roomplanner.params import GenerationParams
from roomplanner.rules import EntranceKind, Rules


@dataclass(frozen=True)
class Context:
    params: GenerationParams
    rules: Rules
    seed: int
    width: int
    height: int
    attempt: int = 0

    def rng(self, stage: str) -> random.Random:
        """Independent, reproducible random stream per stage and attempt."""
        return random.Random(f"{self.seed}:{self.attempt}:{stage}")

    @staticmethod
    def cells(meters: float) -> int:
        return meters_to_cells(meters)

    @staticmethod
    def area_cells(square_meters: float) -> int:
        return round(square_meters / CELL_AREA_M2)


@dataclass(frozen=True)
class PlannedRoom:
    type: str
    cells: frozenset[Cell]


@dataclass(frozen=True)
class EntranceRequest:
    """An exterior door the openings stage must place on `room`'s facade at `side`."""

    kind: EntranceKind
    side: Side
    room: int  # index into FloorPlan.rooms
    hint: Cell  # facade cell of `room` the door should be placed at or near


@dataclass
class FloorPlan:
    level: int
    role: str
    rooms: list[PlannedRoom]
    entrances: list[EntranceRequest] = field(default_factory=list[EntranceRequest])


@dataclass
class BuildingPlan:
    floors: list[FloorPlan]
    warnings: list[str] = field(default_factory=list[str])


class AllocationError(Exception):
    """A required room could not be placed. Retried with another seed."""


class FootprintStrategy(Protocol):
    def footprint(self, ctx: Context) -> frozenset[Cell]: ...


class LayoutStrategy(Protocol):
    def check_feasibility(self, ctx: Context, footprint: frozenset[Cell]) -> list[str]:
        """Problems that make the parameters impossible for this layout, never retried."""
        ...

    def layout(self, ctx: Context, footprint: frozenset[Cell]) -> BuildingPlan: ...


class OpeningsStrategy(Protocol):
    def build(self, ctx: Context, footprint: frozenset[Cell], plan: BuildingPlan) -> list[Floor]:
        """Turn planned rooms into floors with walls, doors and windows."""
        ...
