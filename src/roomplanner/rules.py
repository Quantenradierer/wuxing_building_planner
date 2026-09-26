"""Building rules: YAML room catalogs and building programs. See docs/decisions/0002.

All lengths are in cells, all areas in cells (number of cells covered).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache
from importlib import resources
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from roomplanner.errors import NotSupportedError, RulesError
from roomplanner.params import BuildingType, GenerationParams, Wealth

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


class Cover(StrEnum):
    NONE = "none"
    LIGHT = "light"
    HEAVY = "heavy"


class ObjectSpec(_Strict):
    size: tuple[int, int] = Field(description="Cells along the wall / row, cells deep")
    cover: Cover = Cover.NONE
    walkable: bool = Field(default=False, description="Can be walked over (stairs)")
    glyph: str = Field(min_length=1, max_length=1, description="ASCII debug glyph")


class ObjectCatalog(_Strict):
    objects: dict[str, ObjectSpec]


class Placement(StrEnum):
    WALL = "wall"  # back against a wall, facing into the room
    CORNER = "corner"  # into a corner
    CENTER = "center"  # as close to the room's centre as possible
    SCATTER = "scatter"  # anywhere free
    NEAR_EXIT = "near_exit"  # close to the room's exterior door (checkouts)
    ROWS = "rows"  # parallel rows with aisles, filling the room (shelves, desks)


class FurnitureRule(_Strict):
    object: str
    placement: Placement
    count: int | tuple[int, int] | None = Field(
        default=None, description="How many (default 1); with `per`: lower and upper bound"
    )
    per: int | None = Field(default=None, gt=0, description="One object per this many cells")
    aisle: int = Field(default=3, gt=0, description="rows: free cells between rows")
    margin: int = Field(default=2, ge=0, description="rows: free cells along the walls")

    @property
    def count_range(self) -> tuple[int, int]:
        if self.count is None:
            return (0, 1000) if self.per is not None else (1, 1)
        return (self.count, self.count) if isinstance(self.count, int) else self.count


class RoomTier(_Strict):
    """Per-wealth override of a room; applied after the global wealth multipliers."""

    area: Range | None = None
    min_side: int | None = None


class RoomSpec(_Strict):
    area: Range
    min_side: int = Field(gt=0)
    windows: WindowRule = WindowRule.OPTIONAL
    circulation: bool = Field(default=False, description="Corridor-like; no walls to others")
    cluster: bool = Field(
        default=False, description="Small back room: goes into clusters, not facade slots"
    )
    max_aspect: float = Field(default=2.5, ge=1)
    door_width: int = Field(default=2, gt=0)
    access: list[str] = Field(default=[], description="Preferred room types to enter from")
    transit: bool = Field(default=True, description="Other rooms may be entered through it")
    furniture: list[FurnitureRule] = []
    wealth: dict[Wealth, RoomTier] = {}


class Catalog(_Strict):
    rooms: dict[str, RoomSpec]


class RoomEntry(_Strict):
    room: str
    count: int | tuple[int, int] | None = None
    share: float | None = Field(default=None, gt=0, le=1)
    fill: bool = False
    place: Literal["entrance", "hall"] | None = None
    near: Literal["core", "entrance"] | None = None
    priority: Priority = Priority.NORMAL
    area: Range | None = Field(default=None, description="Overrides the catalog")
    when: str | None = None
    wealth: list[Wealth] | None = Field(default=None, description="Only for these tiers")

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
    BASEMENT = "basement"


class FloorRole(_Strict):
    applies: list[Applies]
    when: str | None = None
    wealth: list[Wealth] | None = Field(default=None, description="Only for these tiers")
    rooms: list[RoomEntry]

    @model_validator(mode="after")
    def _has_fill(self) -> Self:
        _check_expression(self.when)
        if not any(entry.fill for entry in self.rooms):
            raise ValueError("every floor role needs a fill room")
        return self


class CoreEntry(_Strict):
    room: str
    size: Range
    when: str | None = None

    @model_validator(mode="after")
    def _check_when(self) -> Self:
        _check_expression(self.when)
        return self


class EntranceKind(StrEnum):
    MAIN = "main"
    SERVICE = "service"


class EntranceRule(_Strict):
    width: int = Field(gt=0)


class CorridorRule(_Strict):
    width: int = Field(gt=0)
    hallway_width: int = Field(default=3, gt=0, description="Side hallways of clusters")


class FacadeRule(_Strict):
    module: int = Field(gt=0, description="Grid for partitions and windows along facades")
    window: int = Field(gt=0)


class HallRule(_Strict):
    min_depth: int = Field(gt=0, description="Minimum depth of the hall band")


class UnitSpec(_Strict):
    """Subdivision of a unit (e.g. an apartment) spanning a strip from corridor to facade.

    The corridor side holds the entry hall with the `front` rooms beside it, the facade side
    the `back` rooms (the first one behind the hall), then `back_fill` rooms for the rest.
    """

    hall: str = "hallway"
    hall_width: int = Field(default=3, gt=0)
    front: list[str] = []
    back: list[str]
    back_fill: str | None = None


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
    furnishing: str = Field(default="rules", description="Furnishing strategy")
    hall: HallRule | None = Field(default=None, description="Required by the hall layout")
    wealth: dict[Wealth, WealthRule] = Field(default={}, description="Overrides wealth.yaml")


@dataclass(frozen=True)
class Rules:
    program: BuildingProgram
    rooms: dict[str, RoomSpec]
    tiers: dict[Wealth, WealthRule]
    objects: dict[str, ObjectSpec]
    wealth: WealthRule = field(default_factory=WealthRule)  # tier these rules are derived for

    def spec(self, room: str) -> RoomSpec:
        return self.rooms[room]

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
    return {Applies.UPPER}


def variables(params: GenerationParams, level: int = 0) -> dict[str, int]:
    return {
        "floors_above": params.floors_above,
        "floors_below": params.floors_below,
        "floors_total": params.floors_above + params.floors_below,
        "level": level,
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
    rules = Rules(program, rooms, tiers | program.wealth, load_objects())
    _check_references(rules)
    return rules


@cache
def load_objects() -> dict[str, ObjectSpec]:
    """The furniture and fixture catalog shared by all building types."""
    return _load(ObjectCatalog, resources.files("roomplanner") / "data" / "objects.yaml").objects


@cache
def rules_for(building_type: BuildingType, wealth: Wealth) -> Rules:
    """The rules of a building type with one wealth tier applied (see requirements, Wealth)."""
    return apply_wealth(load_rules(building_type), wealth)


def apply_wealth(rules: Rules, wealth: Wealth) -> Rules:
    tier = rules.tiers.get(wealth, WealthRule())

    def scale(area: Range) -> Range:
        return max(1, round(area[0] * tier.area)), max(1, round(area[1] * tier.area))

    rooms: dict[str, RoomSpec] = {}
    for name, spec in rules.rooms.items():
        override = spec.wealth.get(wealth, RoomTier())
        rooms[name] = spec.model_copy(
            update={
                "area": override.area or scale(spec.area),
                "min_side": override.min_side or spec.min_side,
            }
        )

    roles: dict[str, FloorRole] = {}
    for name, role in rules.program.floor_roles.items():
        if role.wealth is not None and wealth not in role.wealth:
            continue
        entries = [
            e.model_copy(update={"area": scale(e.area) if e.area else None})
            for e in role.rooms
            if e.wealth is None or wealth in e.wealth
        ]
        if not any(e.fill for e in entries):
            raise RulesError(f"{rules.program.building}: role {name} has no fill room for {wealth}")
        roles[name] = role.model_copy(update={"rooms": entries})

    corridor = rules.program.corridor
    minimum = rules.rooms["corridor"].min_side if "corridor" in rules.rooms else 1
    width = max(minimum, round(corridor.width * tier.corridor))
    program = rules.program.model_copy(
        update={"floor_roles": roles, "corridor": corridor.model_copy(update={"width": width})}
    )
    return Rules(program, rooms, rules.tiers, rules.objects, tier)


def _data_file(kind: str, name: str) -> Any:
    return resources.files("roomplanner") / "data" / kind / f"{name}.yaml"


def _load[M: BaseModel](model: type[M], path: Any) -> M:
    try:
        return model.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise RulesError(f"{path}: {error}") from error


def _check_references(rules: Rules) -> None:
    program = rules.program
    names = [program.cluster_filler, *(c.room for c in program.core)]
    names += [e.room for role in program.floor_roles.values() for e in role.rooms]
    for unit, spec in program.units.items():
        names += [unit, spec.hall, *spec.front, *spec.back]
        names += [spec.back_fill] if spec.back_fill else []
    names += [a for spec in rules.rooms.values() for a in spec.access]
    unknown = {f.object for s in rules.rooms.values() for f in s.furniture} - set(rules.objects)
    if unknown:
        raise RulesError(f"{program.building}: unknown objects {', '.join(sorted(unknown))}")
    if missing := sorted({n for n in names if n not in rules.rooms}):
        raise RulesError(f"{program.building}: unknown rooms {', '.join(missing)}")


# --- `when:` expressions ------------------------------------------------------------
# A deliberately tiny language: integer variables, + -, comparisons, and/or/not.

_VARIABLES = {"floors_above", "floors_below", "floors_total", "level"}


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
