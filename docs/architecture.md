# Architecture

## Package layout

```
src/roomplanner/
  geometry.py       Cell, Edge, Side, Axis, CELL_SIZE_M
  model.py          Room, Opening, Swing, Floor, Building (immutable dataclasses)
  params.py         GenerationParams (Pydantic) and parameter enums
  generator.py      generate(params) -> Building
  validation.py     hard/soft invariant checks, independent of any algorithm
  serialization.py  JSON contract: to_json / from_json
  errors.py         RoomplannerError hierarchy
  render/ascii.py   minimal debug renderer
  cli.py            Typer CLI
```

```
  rules.py                   Pydantic models for the YAML, loader, `when:` expressions
  data/rooms/*.yaml          room catalogs (common + per building type)
  data/buildings/*.yaml      one program per building type
  pipeline/base.py           Context, intermediate plan, stage protocols
  pipeline/registry.py       strategies by name or `module:Class`
  pipeline/run.py            feasibility check, attempts, validation, best-effort result
  pipeline/footprint.py      footprint strategies
  pipeline/layout/           layout strategies (frame/grid helpers, allocation, corridor)
  pipeline/openings.py       walls, doors, windows
```

## Units

Everything is in cells: lengths in cells, areas in number of cells. `CELL_SIZE_M` (0.5) exists
only so renderers know the physical scale; no code converts meters.

## Coordinates

- The origin is the north-west (top-left) corner. `x` grows east, `y` grows south.
- A **cell** `(x, y)` is the 0.5 m square whose north-west corner is the grid vertex `(x, y)`.
- An **edge** is a unit segment between two grid vertices, stored canonically as
  `Edge(x, y, axis)`:
  - `Axis.H`: from vertex `(x, y)` to `(x+1, y)`, i.e. the north side of cell `(x, y)`,
    separating cells `(x, y-1)` and `(x, y)`.
  - `Axis.V`: from vertex `(x, y)` to `(x, y+1)`, i.e. the west side of cell `(x, y)`,
    separating cells `(x-1, y)` and `(x, y)`.
  - `Edge.of(cell, side)` converts "side of a cell" to the canonical edge.
- A building of `width × height` cells has horizontal edges `x ∈ [0, w)`, `y ∈ [0, h]` and
  vertical edges `x ∈ [0, w]`, `y ∈ [0, h)`.

## Data model

```
Building
  schema_version, width, height (cells), seed, params, warnings
  floors: Floor[]                      sorted by level
Floor
  level                                0 = ground, <0 basement
  footprint: set[Cell]                 interior cells of this floor
  rooms: Room[]                        partition the footprint exactly
  walls: set[Edge]                     every wall edge, openings included
  openings: Opening[]                  cut into walls
Room
  id, type, cells: set[Cell]
Opening
  kind: door | window
  edges: Edge[]                        straight, contiguous run of wall edges
  swing: Swing | None                  doors only
Swing
  towards: Side                        side of the wall the leaf opens into
  hinge: Side                          end of the run the hinge sits at
```

Invariants (checked by `validation.py`):

- Rooms are disjoint and their union is the footprint.
- Every edge between a footprint cell and a non-footprint cell is a wall.
- Walls only exist on edges touching the footprint.
- Every opening's edges are walls; openings never share edges.
- Swings are perpendicular to their wall (`towards`) and parallel to it (`hinge`).

Exterior vs. interior walls, room areas, etc. are derived, not stored.

Layers (furniture, later tactical, security, condition) are added to `Floor` as
separate collections of placed objects. Each layer is produced by its own pipeline stage.

## Pipeline

```
params ─► footprint ─► feasibility check ─► layout (core, corridors, rooms) ─► openings
       ─► furnishing ─► condition ─► validation ─► Building
```

The core is placed by the layout strategy because core and corridors depend on each other.

- Each stage is a **strategy** behind a small `Protocol`, registered by name.
  The building's YAML chooses strategies by name (`layout: corridor`), or by import path
  for custom code (`layout: my_pkg.module:MyLayout`).
- Stages only communicate through the data model. No stage knows how another works.
- Every stage receives its own `random.Random` derived from the run seed and the stage
  name, so changing one stage's randomness does not reshuffle the others.
- Validation is algorithm-agnostic. A hard violation triggers a retry with a derived seed;
  after N retries the best attempt is returned with warnings.

### Corridor layout

Works in local frames: `u` along a part's long axis, `v` across it.

0. **Parts.** A rectangle is one part. An L is split into a *main* bar that touches the street
   (lobby, core, entrances) and a *wing* whose frame runs away from the junction; each wing
   corridor is joined to the main part by a connector stub through the adjacent strip.

1. **Bands.** The depth is split into strips (rows of rooms) and corridors along `u`:
   single-loaded (one strip) for shallow buildings, one central corridor for medium depth,
   `k` parallel corridors ("racetrack") for deep buildings: facade strips keep their
   `strip_depth`, thin back-to-back interior strips between the corridors take the rest.
2. **Skeleton**, identical on all floors: a cross corridor joining parallel corridors, and
   the vertical core as one full-depth slot in a strip (stairwell wrapping the elevator).
3. **Ground floor**: the lobby is a slot in the street-side strip (street on a long side) or a
   slice across the whole building (street on a short end). A service corridor stub reaches
   the service side when no corridor touches it; fallback is an exit from the stairwell.
4. **Allocation** fills the remaining strip segments. Rooms span the full strip depth, except
   small rooms (toilets, storage) which go into *clusters*: a side hallway from the corridor
   with rooms stacked along it. Partition walls on facade strips snap to the facade `module`
   grid, so windows (one per module) never collide with walls.
5. Order: required → normal → optional; fixed counts before `share` rooms, which shrink or
   split instead of crowding others out. `fill` rooms take the rest, preferring rooms that
   need windows on facades and windowless ones inside.
6. Forced intervals (lobby slice, connectors) absorb gaps too small for a room.

Windows follow one facade grid for all floors; each floor omits the windows its own walls,
doors or windowless rooms collide with.

### Wealth

`data/wealth.yaml` holds per-tier multipliers (area, corridor width, furniture density).
`rules_for(type, wealth)` derives the effective rules: scaled room areas, per-room tier
overrides (`wealth:` in the catalog), and room entries / floor roles filtered by their
`wealth:` lists (e.g. the office's executive top floor exists for high and luxury only).

### Units

A room type listed under a program's `units:` (e.g. `apartment`) is allocated like any
full-depth room and then subdivided (`pipeline/layout/units.py`): entry hall on the corridor
side with `front` rooms beside it, `back` rooms along the facade, and — if the strip is deep
enough — a hall running along the unit so every room opens onto it. Rooms carry the unit id
(`Room.unit`, JSON `"unit"`).

### Doors

One door per non-circulation room, committed greedily over all pending rooms: into
circulation first, then into a type from the room's `access` list, then into any connected
room that allows `transit`; ties go to the longest shared wall. Rooms of a unit connect only
within their unit, except the unit's entry room, which opens to circulation.

Planned: the `hall` layout (supermarket).

## JSON contract

`serialization.py` maps the model to a JSON document explicitly (not via reflection),
so internal refactors do not change the contract. Breaking changes bump `schema_version`.

```json
{
  "schema_version": 2,
  "cell_size_m": 0.5,
  "width": 60, "height": 40,          // cells
  "seed": 42,
  "params": { "building_type": "office", "...": "..." },
  "warnings": [],
  "floors": [{
    "level": 0,
    "name": "Ground floor",
    "role": "ground",
    "footprint": [[0, 0], [1, 0], "..."],
    "rooms": [{"id": "0.1", "type": "office", "cells": [[0, 0], "..."]}],
    "walls": [[0, 0, "h"], [0, 0, "v"], "..."],
    "openings": [{
      "kind": "door",
      "edges": [[10, 40, "h"], [11, 40, "h"]],
      "swing": {"towards": "N", "hinge": "W"}
    }]
  }]
}
```

Cells are `[x, y]`, edges are `[x, y, "h" | "v"]`. Lists are sorted for stable output.
Optional fields (e.g. a room's `"unit"`) may be added without a version bump; readers must
ignore unknown fields.

## ASCII debug renderer

Doubled grid: a `w × h` floor becomes `(2w+1) × (2h+1)` characters. Cell `(x, y)` sits at
column `2x+1`, row `2y+1`. Edges and vertices occupy the even positions in between.

| Glyph     | Meaning                            |
|-----------|------------------------------------|
| `-` `\|`  | wall                               |
| `+`       | vertex touching a wall             |
| `D`       | door edge                          |
| `=` `"`   | window edge (horizontal, vertical) |
| digits    | room number, see the legend        |
