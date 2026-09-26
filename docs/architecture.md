# Architecture

## Package layout

```
src/roomplanner/
  geometry.py                Cell, Edge (NamedTuples), Side, Axis, grid helpers, CELL_SIZE_M
  model.py                   Room, Opening, Swing, PlacedObject, Floor, Building
  params.py                  GenerationParams (Pydantic) and parameter enums
  rules.py                   Pydantic models for the YAML, loaders, wealth, `when:` expressions
  generator.py               generate(params) -> Building
  validation.py              hard/soft invariant checks, independent of any algorithm
  serialization.py           JSON contract: to_json / from_json
  errors.py                  RoomplannerError hierarchy
  render/ascii.py            minimal debug renderer
  render/image.py            image renderer (one picture per floor)
  render/theme.py            theme model and loader
  render/shapes.py           procedural object shapes
  cli.py                     Typer CLI
  pipeline/
    base.py                  Context, intermediate plan, stage protocols
    registry.py              strategies by name or `module:Class`
    run.py                   feasibility check, attempts, validation, best-effort result
    footprint.py             footprint strategies: rectangle, l, u
    layout/frame.py          local (u, v) frames, facade grid, bands, intervals
    layout/parts.py          splits a footprint into a main part and wings
    layout/corridor.py       corridor layout (parts, bands, core, lobby, connectors)
    layout/allocation.py     fills strip segments with a floor role's rooms
    layout/units.py          subdivides units (apartments)
    layout/hall.py           hall layout (supermarket), a corridor layout variant
    openings.py              walls, doors, windows
    furnishing.py            furniture and fixtures
  data/
    wealth.yaml              per-tier multipliers
    objects.yaml             furniture and fixture catalog
    themes/*.yaml            image renderer themes (default: neon)
    rooms/*.yaml             room catalogs (common + per building family)
    buildings/*.yaml         one program per building type
```

## Units

Everything is in cells: lengths in cells, areas in number of cells. `CELL_SIZE_M` (0.5) exists
only so renderers know the physical scale; no code converts meters.

## Coordinates

- The origin is the north-west (top-left) corner. `x` grows east, `y` grows south.
- A **cell** `(x, y)` is the square whose north-west corner is the grid vertex `(x, y)`.
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
  schema_version, width, height, seed, params, warnings
  floors: Floor[]                      sorted by level
Floor
  level                                0 = ground, <0 basement
  role                                 floor role from the program (ground, standard, …)
  footprint: set[Cell]                 interior cells of this floor
  rooms: Room[]                        partition the footprint exactly
  walls: set[Edge]                     every wall edge, openings included
  openings: Opening[]                  cut into walls
  objects: PlacedObject[]              furniture and fixtures
Room
  id, type, cells: set[Cell], unit     unit: apartment etc. the room belongs to, if any
Opening
  kind: door | window
  edges: Edge[]                        straight, contiguous run of wall edges
  swing: Swing | None                  doors only
Swing
  towards: Side                        side of the wall the leaf opens into
  hinge: Side                          end of the run the hinge sits at
PlacedObject
  kind, x, y, w, h                     covers cells [x, x+w) × [y, y+h)
  facing: Side                         front of the object, away from its wall
  room                                 id of the room it stands in
  blocking                             false for objects walked over (stairs, elevator car)
```

Model classes are immutable. Exterior vs. interior walls, room areas and door clearances
are derived, not stored.

Invariants checked by `validation.py` (all hard unless noted):

- Rooms are disjoint and their union is the footprint; every cell is reachable (ground
  floor: from an exterior door; other floors: one connected area).
- Every edge between a footprint and a non-footprint cell is a wall; walls touch the
  footprint; openings lie on walls and never share edges; door swings fit their wall.
- With rules: every room is at least `min_side` wide everywhere; core rooms (stairs,
  elevators) occupy the same cells on every floor; rooms that need windows have one (soft).
- Objects lie inside their room and don't overlap; blocking objects keep door clearances
  free and leave the room's free floor connected.

Future layers (tactical markup, security, condition) are added to `Floor` as further
collections, each produced by its own pipeline stage.

## Pipeline

```
params ─► footprint ─► feasibility check ─► layout (core, corridors, rooms) ─► openings
       ─► furnishing ─► [condition, planned] ─► validation ─► Building
```

- Each stage is a **strategy** behind a small `Protocol` (`pipeline/base.py`), registered by
  name. The program YAML selects strategies by name (`layout: corridor`, `furnishing:
  rules`) or by import path for custom code (`layout: my_pkg.module:MyLayout`).
- Stages only communicate through the intermediate plan and the model.
- Randomness: every stage gets its own `random.Random` derived from seed, attempt and stage
  name, so changing one stage does not reshuffle the others. The same parameters and seed
  always give the same building.
- Failure policy (ADR 0004): the layout's feasibility check fails fast for impossible
  input. Otherwise up to 8 attempts run; an attempt whose layout cannot place a required
  room (`AllocationError`) is discarded, hard violations trigger the next attempt. The best
  attempt is returned, with dropped rooms and violations as warnings. If no attempt could
  place the required rooms, generation fails with `InfeasibleError`.
- The core is placed by the layout strategy because core and corridors depend on each other.

### Corridor layout

Works in local frames: `u` along a part's long axis (or away from the junction for a wing),
`v` across it.

1. **Parts** (`layout/parts.py`). A rectangle is one part. Other footprints are cut along
   every line where their outline changes; the *main* part is a maximal rectangle of those
   blocks (preferring one on the street, deep enough, largest), the rest becomes *wings*,
   each attached with one whole end to an earlier part (its parent). Each wing corridor is
   joined to its parent by a connector stub through the parent's adjacent strip.
2. **Bands.** The depth is split into strips (rows of rooms) and corridors along `u`:
   single-loaded (one strip) for shallow parts, one central corridor for medium depth,
   `k` parallel corridors ("racetrack") for deep ones: facade strips keep their
   `strip_depth`, thin back-to-back interior strips between the corridors take the rest.
3. **Skeleton**, identical on all floors: a cross corridor joining parallel corridors, and
   the vertical core as one full-depth slot in a strip (stairwell wrapping the elevator; in
   deep strips a storage room behind it).
4. **Ground floor**: the lobby is a slot in the street-side strip (street on a long side) or a
   slice across the whole part (street on a short end). A service corridor stub reaches the
   service side when no corridor touches it; fallback is an exit from the stairwell.
5. **Allocation** fills the remaining strip segments. Rooms span the full strip depth, except
   small rooms (toilets, storage; rooms marked `cluster: true` or too small for a full-depth
   slot) which go into *clusters*: a side hallway from the corridor with rooms stacked along
   it. Rooms that need windows are never clustered. Leftover modules widen a fill room. Partition walls
   on facade strips snap to the facade `module` grid.
6. **Order**: required → normal → optional; fixed counts before `share` rooms, which shrink
   or split instead of crowding others out. `fill` rooms take the rest, preferring rooms that
   need windows on facades and windowless ones inside. `near: core | entrance` pulls rooms
   towards those anchors.
7. Forced intervals (lobby slice, connectors) absorb gaps too small for a room.

### Hall layout

`layout: hall` subclasses the corridor layout and only changes the main part's bands to
`[hall | service corridor | back-of-house strip]`, hall on the street side (and away from an
L's junction). Each floor role names the hall's room type with `place: hall` (sales floor;
stockroom in the basement). Without a lobby the main entrance opens into the hall.
Connectors that must cross a hall band become a short corridor across its end.

### Units

A room type listed under a program's `units:` (e.g. `apartment`) is allocated like any
full-depth room and then subdivided: entry hall on the corridor side with `front` rooms
beside it, `back` rooms along the facade, and — if the strip is deep enough — a hall running
along the unit so every room opens onto it. Rooms carry the unit id.

### Openings

- Walls separate different rooms, except between two circulation rooms (corridor, lobby).
- One door per non-circulation room, committed greedily over all pending rooms: into
  circulation first, then into a type from the room's `access` list, then into any connected
  room that allows `transit`; ties go to the longest shared wall. Rooms of a unit connect
  only within their unit, except the unit's entry room, which opens to circulation.
- Exterior doors open outwards, at the positions the layout requested.
- Windows follow one facade grid for all floors (so they line up); each floor omits the
  windows its own walls, doors or windowless rooms collide with.

### Wealth

`data/wealth.yaml` holds per-tier multipliers (area, corridor width, furniture density).
`rules_for(type, wealth)` derives the effective rules: scaled room areas, per-room tier
overrides (`wealth:` in the catalog), and room entries / floor roles filtered by their
`wealth:` lists (e.g. the office's executive top floor exists for high and luxury only).

### Furnishing

`furnishing: rules` places the objects listed under a room's `furniture:`. Objects come from
`data/objects.yaml` (size in cells along the wall × deep, cover, `walkable`, ASCII glyph).
Placements: `wall`, `corner`, `center`, `scatter`, `near_exit` (checkouts) and `rows`
(shelves, desks, racks with aisles and cross aisles). Counts are a range or `per: N` (one
object per N cells of room, bounded by `count`), scaled by the tier's `furniture` factor.
Walkable objects (stairs, elevator car, rugs) may cover door clearances and never block. A
placement is rejected if it covers another object or a door's clearance (as deep as the door
is wide) or splits the room's free floor; a ring test around the object avoids most flood
fills.

## JSON contract

`serialization.py` maps the model to a JSON document explicitly (not via reflection),
so internal refactors do not change the contract. Breaking changes bump `schema_version`;
optional fields may be added without a bump, so readers must ignore unknown fields.

```json
{
  "schema_version": 2,
  "cell_size_m": 0.5,
  "width": 60, "height": 40,
  "seed": 42,
  "params": {"building_type": "office", "width": 60, "depth": 40, "wealth": "middle", "...": "..."},
  "warnings": [],
  "floors": [{
    "level": 0,
    "name": "Ground floor",
    "role": "ground",
    "footprint": [[0, 0], [0, 1], "..."],
    "rooms": [{"id": "0.1", "type": "office", "cells": [[0, 0], "..."]},
              {"id": "0.7", "type": "bedroom", "cells": ["..."], "unit": "0-03"}],
    "walls": [[0, 0, "h"], [0, 0, "v"], "..."],
    "openings": [{
      "kind": "door",
      "edges": [[10, 40, "h"], [11, 40, "h"]],
      "swing": {"towards": "S", "hinge": "W"}
    }, {"kind": "window", "edges": [[3, 0, "h"], [4, 0, "h"]]}],
    "objects": [{"kind": "desk", "x": 4, "y": 1, "w": 3, "h": 2, "facing": "S", "room": "0.3"},
                {"kind": "stairs", "x": 20, "y": 1, "w": 6, "h": 4, "facing": "S", "room": "0.2",
                 "blocking": false}]
  }]
}
```

Cells are `[x, y]`, edges are `[x, y, "h" | "v"]`; cell and wall lists are sorted, the
output as a whole is deterministic.

## Image renderer

`render/image.py` draws one RGB image per floor from the model alone (ADR 0009). The image
covers the building plus `padding` cells on each side (default 2 = one 1 m VTT square), at
`cell_px` pixels per cell (default 50). Layers: background, room floors (theme material:
colour, pattern, optional neon accent strip along the walls), grain, blurred shadows of
walls and blocking objects, objects (theme style → shape from `render/shapes.py`), walls
(exterior thicker), windows, doors (leaf opened 90° towards `swing.towards` plus arc; runs
of 4+ cells are double doors), optional labels and grid, then the blurred glow layer is
screened on top. Themes are YAML (`data/themes/neon.yaml`) or any file passed by path.

## ASCII debug renderer

Doubled grid: a `w × h` floor becomes `(2w+1) × (2h+1)` characters. Cell `(x, y)` sits at
column `2x+1`, row `2y+1`; edges and vertices occupy the even positions in between.

| Glyph     | Meaning                                                  |
|-----------|----------------------------------------------------------|
| `-` `\|`  | wall                                                     |
| `+`       | walls meeting at an angle                                |
| `D`       | door edge                                                |
| `=` `"`   | window edge (horizontal, vertical)                       |
| letters   | furniture, see the per-floor `objects:` legend           |
| digits    | room number, see the legend (with the unit in brackets)  |
