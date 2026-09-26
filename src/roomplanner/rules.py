"""Building rules: YAML room catalogs and building programs. See docs/decisions/0002."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from importlib import resources
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from roomplanner.errors import NotSupportedError, RulesError
from roomplanner.params import BuildingType, GenerationParams

type Range = tuple[float, float]


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


class RoomSpec(_Strict):
    area_m2: Range
    min_side_m: float = Field(gt=0)
    windows: WindowRule = WindowRule.OPTIONAL
    circulation: bool = Field(default=False, description="Corridor-like; no walls to others")
    max_aspect: float = Field(default=2.5, ge=1)
    door_width_m: float = Field(default=1.0, gt=0)


class Catalog(_Strict):
    rooms: dict[str, RoomSpec]


class RoomEntry(_Strict):
    room: str
    count: int | tuple[int, int] | None = None
    share: float | None = Field(default=None, gt=0, le=1)
    fill: bool = False
    place: Literal["entrance"] | None = None
    near: Literal["core", "entrance"] | None = None
    priority: Priority = Priority.NORMAL
    area_m2: Range | None = Field(default=None, description="Overrides the catalog")
    when: str | None = None

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
    rooms: list[RoomEntry]

    @model_validator(mode="after")
    def _has_fill(self) -> Self:
        _check_expression(self.when)
        if not any(entry.fill for entry in self.rooms):
            raise ValueError("every floor role needs a fill room")
        return self


class CoreEntry(_Strict):
    room: str
    size_m: Range
    when: str | None = None

    @model_validator(mode="after")
    def _check_when(self) -> Self:
        _check_expression(self.when)
        return self


class EntranceKind(StrEnum):
    MAIN = "main"
    SERVICE = "service"


class EntranceRule(_Strict):
    width_m: float = Field(gt=0)


class CorridorRule(_Strict):
    width_m: float = Field(gt=0)
    hallway_width_m: float = Field(default=1.5, gt=0, description="Side hallways of clusters")


class FacadeRule(_Strict):
    module_m: float = Field(gt=0, description="Grid for partitions and windows along facades")
    window_m: float = Field(gt=0)


class BuildingProgram(_Strict):
    building: BuildingType
    catalogs: list[str]
    layout: str
    corridor: CorridorRule
    strip_depth_m: Range
    facade: FacadeRule
    cluster_filler: str = Field(description="Room type filling leftover space in clusters")
    core: list[CoreEntry] = []
    entrances: dict[EntranceKind, EntranceRule]
    floor_roles: dict[str, FloorRole]


@dataclass(frozen=True)
class Rules:
    program: BuildingProgram
    rooms: dict[str, RoomSpec]

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
    rules = Rules(program, rooms)
    _check_references(rules)
    return rules


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
