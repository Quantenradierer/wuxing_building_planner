# Requirements

Roomplanner generates battle maps of building interiors for Shadowrun / cyberpunk
tabletop games. Output is a structured model (JSON) plus a minimal ASCII debug view.
Image rendering comes later and consumes the JSON.

## Scope

In scope (v1):

- Interior of a single building. No lot, street or neighbouring buildings.
- Structure: exterior and interior walls, doors, windows, stairs/elevators.
- Room semantics: every room has a type and a label.
- Furniture and fixtures.
- Multiple floors above ground and basements.

In scope (v2, see "Roadmap"):

- Security layer: devices (cameras, maglocks, card readers, alarm panels), security rooms
  per tier, door properties (lock, rating, material).
- Condition layer: debris, removed furniture, broken / missing / blocked doors, wall
  breaches, collapsed areas, broken windows; decals in the image renderer.
- Lights: per room type, altered by condition and security.
- Image rendering (procedural, themeable) and VTT export (Universal VTT, Foundry VTT).

Later (the model must allow them without redesign):

- Tactical markup layer (cover, difficult terrain): postponed until it is clear how images
  and VTT exports would use it. `objects.yaml` keeps its `cover` field, unused.
- Surroundings (lot, parking, loading area, street, fire escapes). The canvas may then be
  larger than the footprint; cells outside the footprint are the exterior zone.
- Parking garage type with its own "deck" layout; basement garages.
- Roll20 export, Foundry *Levels* support.

Out of scope: objective markers (paydata, safes, …), scenario design.

## Grid

- 1 cell = 0.5 m × 0.5 m. A classic battle square (1.5 m) is 3×3 cells.
- Walls, doors and windows lie on **edges between cells**, never in cells.
- **All units are cells**: parameters, rule files and output use cells for lengths and
  cell counts for areas. Meters appear only as the scale hint `cell_size_m` in the JSON.

## Parameters

| Parameter       | Values                                              | v1 behaviour                          |
|-----------------|-----------------------------------------------------|---------------------------------------|
| `building_type` | office, apartment, supermarket, clinic              | honoured                              |
| `width`         | cells (east–west)                                   | honoured; bounding box for L shapes   |
| `depth`         | cells (north–south)                                 | honoured; bounding box for L shapes   |
| `floors_above`  | ≥ 1, includes the ground floor                      | honoured                              |
| `floors_below`  | ≥ 0 (basements)                                     | honoured                              |
| `wealth`        | squatter, low, middle, high, luxury                 | honoured                              |
| `condition`     | pristine, maintained, run_down, derelict, ruined    | condition layer (v2)                  |
| `security`      | none, low, corporate, aaa                           | security layer (v2)                   |
| `shape`         | rectangle, l, u, t, z, stepped, irregular           | all; `irregular` picks one by seed    |
| `entrances`     | list of entrance kinds                              | overrides the building type's list    |
| `street_side`   | N, E, S, W (default S)                              | main entrance faces this side         |
| `service_side`  | N, E, S, W (default: opposite of `street_side`)     | service entrances face this side      |
| `seed`          | integer (random if omitted, always recorded)        | full determinism                      |

There are no size presets and no room overrides. Shapes other than the rectangle are unions
of rectangles (no diagonals or curves); every arm is as deep as the layout's main part
(e.g. corridor plus one row of rooms).

Floor numbering: ground floor = 0, upper floors 1, 2, …, basements −1, −2, …
(displayed as B1, B2, …).

## Building types (v1)

| Type        | Layout strategy | Notes                                               |
|-------------|-----------------|-----------------------------------------------------|
| office      | corridor        | lobby/reception, offices, open plan, meeting rooms  |
| clinic      | corridor        | many specialised rooms, surgery floor, wards        |
| apartment   | corridor+units  | flats along a corridor, each subdivided             |
| supermarket | hall            | sales hall per floor, back-of-house strip, loading  |

## Building rules

- Rules are data: YAML files validated with Pydantic.
  - A shared **room catalog** (toilet, storage, stairwell, corridor, …) with area ranges,
    minimum side, window requirement, door width, which rooms it is entered from
    (`access`), whether it may be walked through (`transit`), wealth overrides, furniture.
  - One **program** per building type: layout strategy, corridor and strip sizes, facade
    grid, vertical core, entrances, units, floor roles (matched by ground / upper / top /
    basement plus `when:` and `wealth:`) and room entries with `count`, `share`, `fill` or
    `place`, a priority (`required`, `normal`, `optional`) and `near: core | entrance`.
  - `when:` conditions are a deliberately tiny expression language.
- Logic that data cannot express goes into Python strategies, referenced from the YAML by
  name or import path (e.g. `layout: my_pkg.layouts:Mine`).

## Floors

- The vertical core (stairwells, elevators, shafts) and outer walls are identical on every
  floor. Windows follow one facade grid for all floors (they line up); a floor omits windows
  where its own walls, doors or windowless rooms are.
- The building type assigns each floor a role; each floor's rooms are generated from that
  role's program.

## Entrances

- The building type defines which entrance kinds exist and their widths: `main`, `service`
  (loading dock, ambulance bay, back door), `emergency` (exit from a corridor end or the
  stairwell) and `roof` (hatch or door from the top floor's stairwell; the roof itself is
  not modelled). The `entrances` parameter overrides the type's list. Fire escapes wait for
  surroundings.
- The main entrance faces `street_side`; the service entrance faces `service_side`.

## Openings

- An opening is a door or a window: a contiguous, straight run of wall edges.
- Doors carry a swing: the side they open towards and the hinge end. Doors open into
  rooms; exits open outwards.

## Wealth

Wealth affects room sizes, the room program (rooms restricted to tiers), furniture density
and variety, and corridor widths. Driven by global per-tier multipliers in the catalog, which
rooms and program entries may override or restrict. With fixed building dimensions, bigger
rooms mean fewer rooms.

## Failure handling

1. **Feasibility pre-check**: if the required program (core, entrances, corridors, `required`
   rooms at minimum size) cannot fit a floor, fail immediately with an explanatory error.
2. Otherwise generate. **Hard violations** (unreachable room, misaligned core, room below
   minimum side, …) trigger a retry with a seed derived from the main seed.
3. After N retries, return the best effort. Dropped rooms and **soft violations**
   (unmet adjacency, missing preferred window, …) are returned as warnings.
4. The same parameters and seed always produce the same output, retries included.

## Interface

- Library: `generate(params) -> Building`.
- CLI (Typer): generate to ASCII or JSON; re-render a saved JSON.
- The JSON is a versioned contract (`schema_version`) for the future image renderer.
- The ASCII renderer is a minimal debug view and will not be polished.

## Quality

- Unit tests, snapshot tests (fixed params and seed), property-based tests (Hypothesis)
  that assert the validator's hard invariants for arbitrary valid parameters.
- Tooling: uv, pytest, Hypothesis, ruff, pyright, Python 3.14.

## Rendering and VTT export (v2)

- The image renderer reads only the JSON. Procedural cyberpunk style (dark concrete, neon
  accents per room type), swappable themes (a textured asset pack is just another theme).
  PNG (or WebP), one image per floor, default 50 px per cell. Lights appear as a subtle
  glow; the VTT does the real lighting.
- VTT export: Universal VTT (`.dd2vtt`, one file per floor) first, then Foundry VTT (v12+,
  one scene per floor in an adventure/compendium JSON). Grid size is a per-VTT parameter,
  default 1 m (2 × 2 cells). Walls, doors (locked if they have a lock), windows (block
  movement, not sight or light) and lights are exported; lights can be switched off.
  Foundry stairs and elevators teleport tokens between floors via native Scene Regions.

## Building types (v2)

New: hospital, hotel (capsule hotel as its squatter/low tiers), corp lab, nightclub
(hall), warehouse (hall). Variants of the v1 types, each a separate type with its own
YAML: corp branch office (`corp_office`), street doc (`street_doc`), coffin block
(`coffin_block`), convenience store (`stuffer_shack`).

## Roadmap (v2)

1. U shapes (main bar plus wings attached to their parent part).
2. Layout fixes: leftover space to useful rooms instead of storage, fuller furnishing,
   visible stairs and elevators.
3. Image renderer.
4. Security, condition, lights, emergency and roof entrances.
5. Irregular shapes: `t`, `z`, `stepped`, `irregular`.
6. New building types and variants.
7. VTT export: Universal VTT, then Foundry.

## Milestones (v1)

1. Skeleton, data model, ASCII and JSON output; a hardcoded single-room generator.
2. Engine and office: YAML catalog/programs, pipeline and strategy registry, rectangular
   footprint, core, corridor layout, doors/windows, validator, retries, feasibility check.
3. Multiple floors with roles, basements, wealth, L-shaped footprint.
4. Clinic (corridor) and apartment building (units).
5. Supermarket (hall).
6. Furnishing layer for all four types.
7. Test hardening and docs pass.

All seven milestones are implemented (2026-09-26).

## Known limitations (v1)

- Shapes `t`, `z`, `stepped` and `irregular` are rejected (roadmap step 5).
- No fire escapes (they need surroundings).
- Adjacency preferences are limited to `near: core | entrance` and door `access` lists.
- Very large buildings (≈100 × 100 m, several floors) take a few seconds to generate.
- The ASCII renderer is a debug view; image rendering is the next step.
