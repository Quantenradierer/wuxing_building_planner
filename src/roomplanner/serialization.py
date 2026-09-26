"""The JSON contract. See docs/architecture.md, section "JSON contract".

The mapping is written out by hand on purpose: internal refactors must not change the
format silently. Breaking changes bump SCHEMA_VERSION.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from roomplanner.errors import SchemaError
from roomplanner.geometry import CELL_SIZE_M, Axis, Cell, Edge, Side
from roomplanner.model import (
    SCHEMA_VERSION,
    Building,
    Floor,
    Opening,
    OpeningKind,
    PlacedObject,
    Room,
    Swing,
)
from roomplanner.params import GenerationParams

type JsonObject = dict[str, Any]


def to_json(building: Building) -> str:
    return json.dumps(to_dict(building), separators=(",", ":"))


def from_json(text: str) -> Building:
    return from_dict(json.loads(text))


def to_dict(building: Building) -> JsonObject:
    return {
        "schema_version": building.schema_version,
        "cell_size_m": CELL_SIZE_M,
        "width": building.width,
        "height": building.height,
        "seed": building.seed,
        "params": building.params.model_dump(mode="json"),
        "warnings": list(building.warnings),
        "floors": [_floor_to_dict(f) for f in building.floors],
    }


def from_dict(data: JsonObject) -> Building:
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise SchemaError(f"unsupported schema_version {version!r}, expected {SCHEMA_VERSION}")
    try:
        return Building(
            params=GenerationParams.model_validate(data["params"]),
            seed=data["seed"],
            width=data["width"],
            height=data["height"],
            floors=tuple(_floor_from_dict(f) for f in data["floors"]),
            warnings=tuple(data["warnings"]),
        )
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        raise SchemaError(f"malformed building document: {error}") from error


def _floor_to_dict(floor: Floor) -> JsonObject:
    return {
        "level": floor.level,
        "name": floor.name,
        "role": floor.role,
        "footprint": _cells(floor.footprint),
        "rooms": [_room_to_dict(r) for r in floor.rooms],
        "walls": [_edge(e) for e in sorted(floor.walls)],
        "openings": [_opening_to_dict(o) for o in floor.openings],
        "objects": [_object_to_dict(o) for o in floor.objects],
    }


def _floor_from_dict(data: JsonObject) -> Floor:
    return Floor(
        level=data["level"],
        footprint=_parse_cells(data["footprint"]),
        rooms=tuple(
            Room(r["id"], r["type"], _parse_cells(r["cells"]), r.get("unit")) for r in data["rooms"]
        ),
        walls=frozenset(_parse_edge(e) for e in data["walls"]),
        openings=tuple(_opening_from_dict(o) for o in data["openings"]),
        role=data.get("role", ""),
        objects=tuple(_object_from_dict(o) for o in data.get("objects", [])),
    )


def _object_to_dict(obj: PlacedObject) -> JsonObject:
    return {
        "kind": obj.kind,
        "x": obj.x,
        "y": obj.y,
        "w": obj.w,
        "h": obj.h,
        "facing": obj.facing.value,
        "room": obj.room,
    }


def _object_from_dict(data: JsonObject) -> PlacedObject:
    return PlacedObject(
        data["kind"], data["x"], data["y"], data["w"], data["h"], Side(data["facing"]), data["room"]
    )


def _room_to_dict(room: Room) -> JsonObject:
    result: JsonObject = {"id": room.id, "type": room.type, "cells": _cells(room.cells)}
    if room.unit is not None:
        result["unit"] = room.unit
    return result


def _opening_to_dict(opening: Opening) -> JsonObject:
    result: JsonObject = {"kind": opening.kind.value, "edges": [_edge(e) for e in opening.edges]}
    if opening.swing is not None:
        result["swing"] = {
            "towards": opening.swing.towards.value,
            "hinge": opening.swing.hinge.value,
        }
    return result


def _opening_from_dict(data: JsonObject) -> Opening:
    swing = data.get("swing")
    return Opening(
        kind=OpeningKind(data["kind"]),
        edges=tuple(_parse_edge(e) for e in data["edges"]),
        swing=Swing(Side(swing["towards"]), Side(swing["hinge"])) if swing else None,
    )


def _cells(cells: frozenset[Cell]) -> list[list[int]]:
    return [[c.x, c.y] for c in sorted(cells)]


def _parse_cells(data: list[list[int]]) -> frozenset[Cell]:
    return frozenset(Cell(x, y) for x, y in data)


def _edge(edge: Edge) -> list[int | str]:
    return [edge.x, edge.y, edge.axis.value]


def _parse_edge(data: list[Any]) -> Edge:
    x, y, axis = data
    return Edge(x, y, Axis(axis))
