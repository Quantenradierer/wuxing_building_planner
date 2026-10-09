"""Pipeline contracts: the context, the intermediate plan and the stage protocols.

Stages communicate only through these types and the model. See docs/decisions/0003.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Protocol

from roomplanner.geometry import Axis, Cell, Diagonal, Side
from roomplanner.model import Floor
from roomplanner.params import EntranceKind, GenerationParams
from roomplanner.rules import Rules


@dataclass(frozen=True)
class Context:
    params: GenerationParams
    rules: Rules
    seed: int
    width: int
    height: int
    attempt: int = 0

    @property
    def layout_name(self) -> str:
        return self.params.layout or self.rules.program.layout

    def rng(self, stage: str) -> random.Random:
        """Independent, reproducible random stream per stage and attempt.

        The stage picks the seed: the room choice, the furniture and the security devices each
        have their own (`GenerationParams.rooms_seed` etc.), everything else follows `seed`.
        """
        return random.Random(f"{self._seed_for(stage)}:{self.attempt}:{stage}")

    def _seed_for(self, stage: str) -> int:
        group = stage.split(":", 1)[0]
        own = {
            "assign": self.params.rooms_seed,
            "units": self.params.rooms_seed,
            "allocate": self.params.rooms_seed,
            "furnish": self.params.furniture_seed,
            "security": self.params.security_seed,
        }.get(group)
        return self.seed if own is None else own


@dataclass(frozen=True)
class PlannedRoom:
    type: str
    cells: frozenset[Cell]
    unit: str | None = None  # rooms of one apartment etc. only connect among themselves
    entry: bool = False  # the unit room that opens to the building's circulation
    # Annex (closet, pantry): carved out of this room and entered only through it.
    host: PlannedRoom | None = field(default=None, compare=False)
    leftover: bool = False  # a storeroom filling space no room fits; neighbours may absorb it
    front: Side | None = None  # the wall its door goes in if it can (stalls: towards the passage)
    hub: bool = False  # doors and walls treat it as circulation (a hall without a corridor)
    hallway: bool = False  # a cluster's side hallway: ends after its last door
    sealed: bool = False  # keeps its shape: no leftover piece joins it


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
    cut: frozenset[Cell] = frozenset()  # footprint cells this floor lacks (under a balcony's sky)
    # 45 degree walls between rooms (a diagonal corridor's sides): the cell is one room's, the
    # cut triangle the neighbouring room's
    diagonals: frozenset[Diagonal] = frozenset()


@dataclass
class BuildingPlan:
    floors: list[FloorPlan]
    warnings: list[str] = field(default_factory=list[str])
    # Facade grid per wall direction: (absolute offset, module). Partitions sit on grid
    # points, so windows are placed between them. Directions without a grid centre windows.
    facade_grid: dict[Axis, tuple[int, int]] = field(default_factory=dict[Axis, tuple[int, int]])


class AllocationError(Exception):
    """A required room could not be placed. Retried with another seed."""


class FootprintStrategy(Protocol):
    def footprint(self, ctx: Context) -> frozenset[Cell]: ...


class LayoutStrategy(Protocol):
    def main_min_depth(self, ctx: Context) -> int:
        """Smallest depth of the main part (footprints size L-shape arms with it)."""
        ...

    def check_feasibility(self, ctx: Context, footprint: frozenset[Cell]) -> list[str]:
        """Problems that make the parameters impossible for this layout, never retried."""
        ...

    def layout(self, ctx: Context, footprint: frozenset[Cell]) -> BuildingPlan: ...


class FurnishingStrategy(Protocol):
    def furnish(self, ctx: Context, floors: list[Floor]) -> list[Floor]:
        """Return the floors with furniture and fixtures (`Floor.objects`) added."""
        ...


class LayerStrategy(Protocol):
    """Lights, security, condition: change finished floors (after furnishing)."""

    def apply(self, ctx: Context, floors: list[Floor]) -> list[Floor]: ...


class OpeningsStrategy(Protocol):
    def build(self, ctx: Context, footprint: frozenset[Cell], plan: BuildingPlan) -> list[Floor]:
        """Turn planned rooms into floors with walls, doors and windows."""
        ...
