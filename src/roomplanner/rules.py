"""Building rules: YAML room catalogs and building programs. See docs/decisions/0002.

All lengths are in cells, all areas in cells (number of cells covered).
"""

from __future__ import annotations

import ast
import zlib
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache
from importlib import resources
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from roomplanner.errors import NotSupportedError, RulesError
from roomplanner.geometry import Side
from roomplanner.params import BuildingType, EntranceKind, GenerationParams, Security, Wealth

type Range = tuple[int, int]


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class WindowRule(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    FORBIDDEN = "forbidden"


class Priority(StrEnum):
    REQUIRED = "required"
    NORMAL = "normal"
    OPTIONAL = "optional"


class ObjectSpec(_Strict):
    size: tuple[int, int] = Field(description="Cells along the wall / row, cells deep")
    walkable: bool = Field(default=False, description="Can be walked over (stairs)")
    glyph: str = Field(min_length=1, max_length=1, description="ASCII debug glyph")


class GroupPart(_Strict):
    object: str
    at: tuple[int, int] = Field(description="Position in the group (along, deep), facing S")
    facing: Side = Field(default=Side.S, description="Relative to the group; S = the group's")


class GroupSpec(_Strict):
    """Objects placed together as one (a table with its chairs, a desk with its chair).

    Drawn for a group against a wall to the north (facing S); placing it rotates it.
    """

    size: tuple[int, int] = Field(description="Cells along the wall / row, cells deep")
    parts: list[GroupPart] = Field(min_length=1)


class RoomTemplate(GroupSpec):
    """A small room laid out in advance (a stall, a coffin pod): its size and objects, drawn
    with the door in the south wall (the group's front); rooms of its size and door
    position get exactly these objects, turned or mirrored."""

    door: int = Field(ge=0, description="First cell of the door along the south wall")


class ObjectCatalog(_Strict):
    objects: dict[str, ObjectSpec]
    groups: dict[str, GroupSpec] = {}


class Placement(StrEnum):
    WALL = "wall"  # back against a wall, facing into the room
    CORNER = "corner"  # into a corner
    CENTER = "center"  # as close to the room's centre as possible
    SCATTER = "scatter"  # anywhere free
    NEAR_EXIT = "near_exit"  # close to the room's exterior door (checkouts)
    ROWS = "rows"  # parallel rows with aisles, filling the room (shelves, desks)
    BACK = "back"  # against the wall farthest from the doors, same spot every time
    END = "end"  # middle of the short wall farthest from the doors (altar), else like back
    FIXED = "fixed"  # the same wall spot on every floor, whatever the doors (ramps)
    AT = "at"  # beside each object of kind `at`, front first (chairs at desks and tables)
    GRID = "grid"  # on one lattice, all alike, from the walls inwards (cafe tables)
    FACING_EXIT = "facing_exit"  # on the entrance's axis, facing it (reception desk)
    FILL = "fill"  # the room's width at the far wall, facing the door (stairs, elevator car)
    PERIMETER = "perimeter"  # side by side along every wall, as many as fit (vending machines)
    AXIS = "axis"  # on a wall at an end of the biggest object's long axis, facing it (screen)


class FurnitureRule(_Strict):
    object: str
    placement: Placement
    count: int | tuple[int, int] | None = Field(
        default=None, description="How many (default 1); with `per`: lower and upper bound"
    )
    per: int | None = Field(default=None, gt=0, description="One object per this many cells")
    per_room: tuple[str, int] | None = Field(
        default=None,
        description="One object per this many rooms of a type opening into the room, "
        "rounded up and bounded by `count`, never scaled (a sink per two stalls)",
    )
    aisle: int = Field(default=3, gt=0, description="rows, grid: free cells between rows")
    margin: int = Field(
        default=2,
        ge=0,
        description="rows, grid: cells along walls; facing_exit: past the door",
    )
    paired: bool = Field(default=False, description="rows: back to back pairs, aisle after each")
    block: int = Field(default=12, gt=0, description="rows: cells between cross aisles")
    head: str | None = Field(
        default=None,
        description="`object` is a pair of desks, `count` counts desks; an odd one (or one "
        "left over when no wall takes another pair) turns a pair into this group",
    )
    toward: str | None = Field(
        default=None, description="rows: face the object of this kind placed before (the altar)"
    )
    at: str | None = Field(default=None, description="at: the object kind to stand beside")
    beside: list[Literal["front", "back", "flanks"]] = Field(
        default=["front", "back", "flanks"], description="at: which sides of the target, in turn"
    )
    choose: list[str] = Field(
        default=[],
        description="Other objects or groups for the same place: the largest of these and "
        "`object` whose size plus `clearance` on every side fits the room (else the largest "
        "that fits at all) is used",
    )
    clearance: int = Field(default=2, ge=0, description="choose: free cells around it")
    sideways: bool = Field(
        default=False,
        description="perimeter: then also lengthwise along the walls, head to foot (sleep "
        "pods in thin rooms)",
    )
    landing: int = Field(default=0, ge=0, description="fill: free cells kept on the door side")
    reach: int | None = Field(default=None, gt=0, description="fill: at most this many deep")
    near_room: str | None = Field(
        default=None,
        description="wall: as close as possible to a room of this type (the stage by backstage)",
    )
    line: bool = Field(
        default=False,
        description="wall: side by side along one wall, the row as long as fits and nearest "
        "the room's entrance (the sinks of a public toilet)",
    )
    not_against: list[str] = Field(
        default=[],
        description="wall: never backed against a wall to a room of these types (stalls)",
    )
    limit: int | None = Field(
        default=None, gt=0, description="at: at most this many in all, not per target"
    )
    front_clear: int = Field(
        default=0,
        ge=0,
        description="Free floor kept in front of it, this many cells deep and as wide as it "
        "plus half that on each side (the dance floor before the DJ booth)",
    )
    wealth: list[Wealth] | None = Field(default=None, description="Only for these tiers")
    security: list[Security] | None = Field(default=None, description="Only for these levels")

    @model_validator(mode="after")
    def _at_needs_target(self) -> Self:
        if (self.placement is Placement.AT) != (self.at is not None):
            raise ValueError(f"'{self.object}': `at` goes with placement 'at' and only with it")
        if self.toward is not None and self.placement is not Placement.ROWS:
            raise ValueError(f"'{self.object}': `toward` goes with placement 'rows' only")
        if (self.line or self.not_against) and self.placement is not Placement.WALL:
            raise ValueError(f"'{self.object}': `line` and `not_against` go with 'wall' only")
        if self.limit is not None and self.placement is not Placement.AT:
            raise ValueError(f"'{self.object}': `limit` goes with placement 'at' only")
        return self

    @property
    def count_range(self) -> tuple[int, int]:
        if self.count is None:
            return (0, 1000) if self.per is not None else (1, 1)
        return (self.count, self.count) if isinstance(self.count, int) else self.count


class RoomTier(_Strict):
    """Per-wealth override of a room; applied after the global wealth multipliers."""

    area: Range | None = None
    min_side: int | None = None


class StallRule(_Strict):
    """Cubicles along one wall of a public toilet, each a small room entered from it."""

    room: str = Field(description="Room type of a stall")
    width: int | None = Field(
        default=None, gt=0, description="Cells along the wall (default: the stall's template)"
    )
    depth: int | None = Field(
        default=None, gt=0, description="Cells from the wall (default: the stall's template)"
    )
    max: int = Field(default=6, gt=0, description="Stalls per row")
    sides: Literal[1, 2] = Field(
        default=1, description="2: a row on each of two opposite walls, the passage between"
    )
    passage: int | None = Field(
        default=None, gt=0, description="Free cells in front of the stalls (default: min_side)"
    )
    rest_area: int = Field(
        default=0, ge=0, description="Cells the room keeps besides its stalls (sinks)"
    )
    single: str = Field(description="Room type if not even one stall fits")
    rest: str | None = Field(
        default=None, description="Room type of what's left in front of the stalls (default: same)"
    )


class RoomSpec(_Strict):
    area: Range
    min_side: int = Field(gt=0)
    windows: WindowRule = WindowRule.OPTIONAL
    circulation: bool = Field(default=False, description="Corridor-like; no walls to others")
    outdoor: bool = Field(
        default=False,
        description="Open air (balcony): the rooms beside it get windows onto it, its facade "
        "is a railing",
    )
    open: float = Field(
        default=0.0,
        ge=0,
        le=1,
        description="Chance a room of this type is open to the corridor (no walls to it)",
    )
    cluster: bool = Field(
        default=False, description="Small back room: goes into clusters, not facade slots"
    )
    max_aspect: float = Field(default=2.5, ge=1)
    door_width: int = Field(default=2, gt=0)
    door_opens_out: bool = Field(
        default=False,
        description="Its door swings out of it (coffin units): no door clearance inside",
    )
    access: list[str] = Field(default=[], description="Preferred room types to enter from")
    vestibule: str | None = Field(
        default=None, description="Entered only through this room type, placed beside it"
    )
    next_to: list[str] = Field(default=[], description="Placed next to these room types")
    connect: list[str] = Field(
        default=[], description="Also a direct door to adjacent rooms of these types"
    )
    stalls: StallRule | None = None
    wealth_shift: int = Field(
        default=0,
        ge=-4,
        le=4,
        description="Look of the room's objects, in wealth tiers from the building's",
    )
    annex: float = Field(
        default=0.5,
        ge=0,
        le=1,
        description="Cluster rooms: chance to become a closet of a host from `access`",
    )
    transit: bool = Field(default=True, description="Other rooms may be entered through it")
    facade_door: int | None = Field(
        default=None,
        gt=0,
        description="Ground floor: its own exterior door this wide (vehicles, deliveries)",
    )
    templates: list[RoomTemplate] = Field(
        default=[], description="Fixed layouts by size; furniture rules for other shapes"
    )
    furniture: list[FurnitureRule] = []
    wealth: dict[Wealth, RoomTier] = {}


class Catalog(_Strict):
    rooms: dict[str, RoomSpec]


class RoomEntry(_Strict):
    room: str
    count: int | tuple[int, int] | None = None
    share: float | None = Field(default=None, gt=0, le=1)
    fill: bool = False
    weight: float = Field(
        default=1.0, gt=0, description="fill: how often it is picked among the floor's fill rooms"
    )
    limit: int | None = Field(
        default=None, gt=0, description="fill: at most this many picked per floor (copy rooms)"
    )
    place: Literal["entrance", "hall"] | None = None
    near: Literal["core", "entrance", "service"] | None = None
    priority: Priority = Priority.NORMAL
    area: Range | None = Field(default=None, description="Overrides the catalog")
    when: str | None = None
    wealth: list[Wealth] | None = Field(default=None, description="Only for these tiers")
    security: list[Security] | None = Field(default=None, description="Only for these levels")

    @model_validator(mode="after")
    def _one_quantity(self) -> Self:
        given = [self.count is not None, self.share is not None, self.fill, self.place is not None]
        if sum(given) != 1:
            raise ValueError(f"'{self.room}': exactly one of count, share, fill, place")
        _check_expression(self.when)
        return self

    @property
    def count_range(self) -> tuple[int, int]:
        if self.count is None:
            return (1, 1)
        return (self.count, self.count) if isinstance(self.count, int) else self.count


class Applies(StrEnum):
    GROUND = "ground"
    UPPER = "upper"
    TOP = "top"
    BELOW_TOP = "below_top"  # the floor under the top one (also `upper`)
    BASEMENT = "basement"


class BalconyRule(_Strict):
    room: str = "balcony"
    depth: int = Field(default=5, gt=0, description="strip, corner: cells deep, from the facade")
    share: float = Field(
        default=0.5, gt=0, le=1, description="strip: of the longest free facade stretch"
    )
    modules: int = Field(default=3, gt=0, description="room, corner: facade modules wide")
    kinds: list[Literal["strip", "room", "corner"]] = Field(
        default=["strip"],
        description="Picked per building: a strip along the facade, a room-sized loggia the "
        "whole row deep, or a corner open on two sides",
    )


class FloorRole(_Strict):
    applies: list[Applies]
    when: str | None = None
    wealth: list[Wealth] | None = Field(default=None, description="Only for these tiers")
    security: list[Security] | None = Field(default=None, description="Only for these levels")
    core_back: str | None = Field(
        default=None, description="Overrides the program's `core_back` on these floors"
    )
    balcony: BalconyRule | None = Field(
        default=None, description="A balcony cut from a facade strip; the rooms move in"
    )
    roof: str | None = Field(
        default=None,
        description="A flat roof: the whole floor is this open-air room around the core "
        "(stair housing, lift shafts); `rooms` may stay empty",
    )
    rooms: list[RoomEntry] = []

    @model_validator(mode="after")
    def _has_fill(self) -> Self:
        _check_expression(self.when)
        if self.roof is None and not any(entry.fill for entry in self.rooms):
            raise ValueError("every floor role needs a fill room (or a `roof`)")
        return self


class CoreEntry(_Strict):
    room: str
    size: Range
    when: str | None = None
    place: Literal["hall"] | None = Field(
        default=None,
        description="hall: in the hall against its back edge instead of the core slot "
        "(public stairs on a sales floor; hall layout only)",
    )

    @model_validator(mode="after")
    def _check_when(self) -> Self:
        _check_expression(self.when)
        return self


class EntranceRule(_Strict):
    width: int = Field(gt=0)


DEFAULT_ENTRANCE_WIDTH = 2


class CorridorRule(_Strict):
    width: int = Field(gt=0)
    hallway_width: int = Field(default=3, gt=0, description="Side hallways of clusters")


class FacadeRule(_Strict):
    module: int = Field(gt=0, description="Grid for partitions and windows along facades")
    window: int = Field(gt=0)


class FoyerRule(_Strict):
    room: str = Field(description="Room type of the foyer (narthex)")
    depth: int = Field(gt=0, description="Cells from the street facade into the hall")


class HallRule(_Strict):
    min_depth: int = Field(gt=0, description="Minimum depth of the hall band")
    foyer: FoyerRule | None = Field(
        default=None, description="Ground floor: a foyer cut off the hall at the street side"
    )
    corridor_from: int = Field(
        default=0,
        ge=0,
        description="Building length from which a service corridor runs between hall and back "
        "of house; in shorter buildings the back rooms open onto the hall",
    )


class UnitSpec(_Strict):
    """Subdivision of a unit (e.g. an apartment) spanning a strip from corridor to facade.

    The corridor side holds the entry hall with the `front` rooms beside it, the facade side
    the `back` rooms (the first one behind the hall), then `back_fill` rooms for the rest.
    Front rooms wider than their maximum area allows leave the spare width to `front_fill`
    rooms (closets), if given.
    """

    hall: str = "hallway"
    hall_width: int = Field(default=3, gt=0)
    hall_in_back: bool = Field(
        default=False,
        description="No hall room: back[0] reaches the corridor and the front rooms open into "
        "it (a hotel room's entry nook)",
    )
    front: list[str] = []
    back: list[str]
    back_fill: str | None = None
    front_fill: str | None = None


class WealthRule(_Strict):
    """Multipliers for one wealth tier."""

    area: float = Field(default=1.0, gt=0)
    corridor: float = Field(default=1.0, gt=0)
    furniture: float = Field(default=1.0, ge=0)


class WealthTable(_Strict):
    tiers: dict[Wealth, WealthRule]


class BuildingProgram(_Strict):
    building: BuildingType
    catalogs: list[str]
    layout: str
    corridor: CorridorRule
    strip_depth: Range
    facade: FacadeRule
    cluster_filler: str = Field(description="Room type filling leftover space in clusters")
    core: list[CoreEntry] = []
    entrances: dict[EntranceKind, EntranceRule]
    floor_roles: dict[str, FloorRole]
    units: dict[str, UnitSpec] = Field(default={}, description="Room types that are units")
    core_back: str | None = Field(
        default=None,
        description="Room behind the core in strips too deep for the stairwell "
        "(default: the cluster filler)",
    )
    service_stub: bool = Field(
        default=False,
        description="Reserve a back room for the back door on the service facade (the first "
        "ground-floor fill room among `service_rooms`), else carve a corridor stub to it; "
        "instead of any room that happens to be there",
    )
    service_rooms: list[str] = Field(
        default=[
            "loading_bay",
            "stockroom",
            "storage",
            "utility",
            "staff_room",
            "laundry",
            "kitchenette",
            "commercial_kitchen",
            "backstage",
            "security_room",
            "cold_storage",
        ],
        description="Room types the service entrance prefers to open into, best first",
    )
    furnishing: str = Field(default="rules", description="Furnishing strategy")
    lighting: str = Field(default="rules", description="Lights strategy")
    security: str = Field(default="rules", description="Security layer strategy")
    condition: str = Field(default="rules", description="Condition layer strategy")
    hall: HallRule | None = Field(default=None, description="Required by the hall layout")
    wealth: dict[Wealth, WealthRule] = Field(default={}, description="Overrides wealth.yaml")


@dataclass(frozen=True)
class Rules:
    program: BuildingProgram
    rooms: dict[str, RoomSpec]
    tiers: dict[Wealth, WealthRule]
    objects: dict[str, ObjectSpec]
    wealth: WealthRule = field(default_factory=WealthRule)  # tier these rules are derived for
    groups: dict[str, GroupSpec] = field(default_factory=dict[str, GroupSpec])

    def spec(self, room: str) -> RoomSpec:
        return self.rooms[room]

    def stall_size(self, rule: StallRule) -> tuple[int, int]:
        """(along the wall, deep) of a stall: the rule's, else its first template's."""
        templates = self.spec(rule.room).templates
        along, deep = templates[0].size if templates else (2, 3)
        return rule.width or along, rule.depth or deep

    def entrances(self, params: GenerationParams) -> dict[EntranceKind, EntranceRule]:
        """The entrances to build: the program's, or the parameter's list (main implied)."""
        program = self.program.entrances
        if params.entrances is None:
            return dict(program)
        kinds = [EntranceKind.MAIN, *(k for k in params.entrances if k is not EntranceKind.MAIN)]
        return {k: program.get(k, EntranceRule(width=DEFAULT_ENTRANCE_WIDTH)) for k in kinds}

    def active_core(self, params: GenerationParams) -> list[CoreEntry]:
        return [c for c in self.program.core if evaluate(c.when, variables(params))]

    def role_for(self, level: int, params: GenerationParams) -> tuple[str, FloorRole]:
        tags = _level_tags(level, params)
        for name, role in self.program.floor_roles.items():
            if tags & set(role.applies) and evaluate(role.when, variables(params, level)):
                return name, role
        raise RulesError(f"{self.program.building}: no floor role for level {level}")


def _level_tags(level: int, params: GenerationParams) -> set[Applies]:
    if level < 0:
        return {Applies.BASEMENT}
    if level == 0:
        return {Applies.GROUND}
    if level == params.floors_above - 1:
        return {Applies.TOP}
    if level == params.floors_above - 2:
        return {Applies.UPPER, Applies.BELOW_TOP}
    return {Applies.UPPER}


def variables(params: GenerationParams, level: int = 0) -> dict[str, int]:
    return {
        "floors_above": params.floors_above,
        "floors_below": params.floors_below,
        "floors_total": params.floors_above + params.floors_below,
        "level": level,
        "width": params.width,
        "depth": params.depth,
        # 0-99, fixed per building: for "sometimes" (a penthouse on half the towers)
        "roll": zlib.crc32(f"{params.seed}:roll".encode()) % 100,
    }


@cache
def load_rules(building_type: BuildingType) -> Rules:
    path = _data_file("buildings", building_type.value)
    if not path.is_file():
        raise NotSupportedError(f"building type '{building_type}' is not implemented yet")
    program = _load(BuildingProgram, path)
    rooms: dict[str, RoomSpec] = {}
    for name in program.catalogs:
        rooms |= _load(Catalog, _data_file("rooms", name)).rooms
    tiers = _load(WealthTable, resources.files("roomplanner") / "data" / "wealth.yaml").tiers
    rules = Rules(program, rooms, tiers | program.wealth, load_objects(), groups=load_groups())
    _check_references(rules)
    return rules


@cache
def load_objects() -> dict[str, ObjectSpec]:
    """The furniture and fixture catalog shared by all building types."""
    return _load(ObjectCatalog, resources.files("roomplanner") / "data" / "objects.yaml").objects


@cache
def load_groups() -> dict[str, GroupSpec]:
    """Object groups (tables with chairs, …); every part lies inside the group, no overlaps."""
    catalog = _load(ObjectCatalog, resources.files("roomplanner") / "data" / "objects.yaml")
    for name, group in catalog.groups.items():
        taken: set[tuple[int, int]] = set()
        for part in group.parts:
            if part.object not in catalog.objects:
                raise RulesError(f"group {name}: unknown object {part.object}")
            along, deep = catalog.objects[part.object].size
            w, h = (along, deep) if part.facing in (Side.N, Side.S) else (deep, along)
            u, v = part.at
            cells = {(u + i, v + j) for i in range(w) for j in range(h)}
            if u < 0 or v < 0 or u + w > group.size[0] or v + h > group.size[1]:
                raise RulesError(f"group {name}: {part.object} sticks out")
            if cells & taken:
                raise RulesError(f"group {name}: {part.object} overlaps another part")
            taken |= cells
    return catalog.groups


@cache
def rules_for(
    building_type: BuildingType, wealth: Wealth, security: Security = Security.LOW
) -> Rules:
    """The rules of a building type for one wealth tier and security level."""
    return apply_wealth(load_rules(building_type), wealth, security)


def apply_wealth(rules: Rules, wealth: Wealth, security: Security = Security.LOW) -> Rules:
    tier = rules.tiers.get(wealth, WealthRule())

    def scale(area: Range) -> Range:
        return max(1, round(area[0] * tier.area)), max(1, round(area[1] * tier.area))

    rooms: dict[str, RoomSpec] = {}
    for name, spec in rules.rooms.items():
        override = spec.wealth.get(wealth, RoomTier())
        furniture = [
            r
            for r in spec.furniture
            if (r.wealth is None or wealth in r.wealth)
            and (r.security is None or security in r.security)
        ]
        rooms[name] = spec.model_copy(
            update={
                "area": override.area or scale(spec.area),
                "min_side": override.min_side or spec.min_side,
                "furniture": furniture,
            }
        )

    roles: dict[str, FloorRole] = {}
    for name, role in rules.program.floor_roles.items():
        if role.wealth is not None and wealth not in role.wealth:
            continue
        if role.security is not None and security not in role.security:
            continue
        entries = [
            e.model_copy(update={"area": scale(e.area) if e.area else None})
            for e in role.rooms
            if (e.wealth is None or wealth in e.wealth)
            and (e.security is None or security in e.security)
        ]
        if role.roof is None and not any(e.fill for e in entries):
            raise RulesError(f"{rules.program.building}: role {name} has no fill room for {wealth}")
        roles[name] = role.model_copy(update={"rooms": entries})

    corridor = rules.program.corridor
    minimum = rules.rooms["corridor"].min_side if "corridor" in rules.rooms else 1
    width = max(minimum, round(corridor.width * tier.corridor))
    program = rules.program.model_copy(
        update={"floor_roles": roles, "corridor": corridor.model_copy(update={"width": width})}
    )
    return Rules(program, rooms, rules.tiers, rules.objects, tier, rules.groups)


def _data_file(kind: str, name: str) -> Any:
    return resources.files("roomplanner") / "data" / kind / f"{name}.yaml"


def _load[M: BaseModel](model: type[M], path: Any) -> M:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise RulesError(f"{path}: {error}") from error
    return load_yaml(model, text, str(path))


def load_yaml[M: BaseModel](model: type[M], text: str, origin: str) -> M:
    """Validate a YAML document against a model; errors become RulesError."""
    try:
        return model.model_validate(yaml.safe_load(text))
    except (yaml.YAMLError, ValidationError) as error:
        raise RulesError(f"{origin}: {error}") from error


def _check_references(rules: Rules) -> None:
    program = rules.program
    names = [program.cluster_filler, *(c.room for c in program.core)]
    names += [program.core_back] if program.core_back else []
    names += [r.core_back for r in program.floor_roles.values() if r.core_back]
    names += [r.balcony.room for r in program.floor_roles.values() if r.balcony]
    names += [r.roof for r in program.floor_roles.values() if r.roof]
    names += [e.room for role in program.floor_roles.values() for e in role.rooms]
    for unit, spec in program.units.items():
        names += [unit, spec.hall, *spec.front, *spec.back]
        names += [spec.back_fill] if spec.back_fill else []
        names += [spec.front_fill] if spec.front_fill else []
    # `access` may name room types other buildings have; unknown ones are ignored.
    used = {f.object for s in rules.rooms.values() for f in s.furniture}
    used |= {c for s in rules.rooms.values() for f in s.furniture for c in f.choose}
    used |= {f.at for s in rules.rooms.values() for f in s.furniture if f.at}
    used |= {f.toward for s in rules.rooms.values() for f in s.furniture if f.toward}
    used |= {p.object for s in rules.rooms.values() for t in s.templates for p in t.parts}
    unknown = used - set(rules.objects) - set(rules.groups)
    for name, spec in rules.rooms.items():
        for template in spec.templates:
            if template.door + spec.door_width > template.size[0]:
                raise RulesError(f"{program.building}: {name}: template door beyond its wall")
        if spec.stalls is not None:
            names += [spec.stalls.room, spec.stalls.single, spec.stalls.rest or spec.stalls.room]
    if unknown:
        raise RulesError(f"{program.building}: unknown objects {', '.join(sorted(unknown))}")
    if missing := sorted({n for n in names if n not in rules.rooms}):
        raise RulesError(f"{program.building}: unknown rooms {', '.join(missing)}")


# --- `when:` expressions ------------------------------------------------------------
# A deliberately tiny language: integer variables, + -, comparisons, and/or/not.

_VARIABLES = {"floors_above", "floors_below", "floors_total", "level", "width", "depth", "roll"}


def evaluate(expression: str | None, values: dict[str, int]) -> bool:
    if expression is None:
        return True
    return bool(_eval(ast.parse(expression, mode="eval").body, values))


def _check_expression(expression: str | None) -> None:
    if expression is None:
        return
    try:
        _eval(ast.parse(expression, mode="eval").body, dict.fromkeys(_VARIABLES, 0))
    except SyntaxError as error:
        raise ValueError(f"invalid expression {expression!r}: {error.msg}") from error


def _eval(node: ast.expr, values: dict[str, int]) -> int | bool:
    match node:
        case ast.Constant(value=bool() | int() as value):
            return value
        case ast.Name(id=name) if name in values:
            return values[name]
        case ast.BinOp(left=left, op=ast.Add(), right=right):
            return _eval(left, values) + _eval(right, values)
        case ast.BinOp(left=left, op=ast.Sub(), right=right):
            return _eval(left, values) - _eval(right, values)
        case ast.UnaryOp(op=ast.Not(), operand=operand):
            return not _eval(operand, values)
        case ast.BoolOp(op=ast.And(), values=operands):
            return all(_eval(o, values) for o in operands)
        case ast.BoolOp(op=ast.Or(), values=operands):
            return any(_eval(o, values) for o in operands)
        case ast.Compare(left=left, ops=ops, comparators=comparators):
            current = _eval(left, values)
            for op, comparator in zip(ops, comparators, strict=True):
                following = _eval(comparator, values)
                if not _compare(op, current, following):
                    return False
                current = following
            return True
        case _:
            raise ValueError(f"unsupported expression: {ast.unparse(node)!r}")


def _compare(op: ast.cmpop, a: int | bool, b: int | bool) -> bool:
    match op:
        case ast.Eq():
            return a == b
        case ast.NotEq():
            return a != b
        case ast.Lt():
            return a < b
        case ast.LtE():
            return a <= b
        case ast.Gt():
            return a > b
        case ast.GtE():
            return a >= b
        case _:
            raise ValueError(f"unsupported comparison {type(op).__name__}")
