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

Later (the model must allow them without redesign):

- Tactical markup layer (cover, difficult terrain).
- Security layer (cameras, maglocks, guard posts, …).
- Condition layer (debris, blocked doors, holes in walls).
- Surroundings (lot, parking, loading area, street). The canvas may then be larger
  than the footprint; cells outside the footprint are the exterior zone.
- Image rendering.

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
| `condition`     | pristine, maintained, run_down, derelict, ruined    | validated and stored only             |
| `security`      | none, low, corporate, aaa                           | validated and stored only             |
| `shape`         | rectangle, l, u, irregular                          | rectangle and L; others are rejected  |
| `street_side`   | N, E, S, W (default S)                              | main entrance faces this side         |
| `service_side`  | N, E, S, W (default: opposite of `street_side`)     | service entrances face this side      |
| `seed`          | integer (random if omitted, always recorded)        | full determinism                      |

There are no size presets and no room overrides. L shapes need both arms as deep as the
layout's main part (e.g. corridor plus one row of rooms).

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

- The building type defines which entrance kinds exist and their widths. v1: `main` and
  `service` (loading dock, ambulance bay, back door). Emergency exits and fire escapes are
  planned.
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

## Milestones

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

- Shapes `u` and `irregular` are rejected; `condition` and `security` have no effect yet.
- Only `main` and `service` entrances; no fire escapes or emergency exits.
- Adjacency preferences are limited to `near: core | entrance` and door `access` lists.
- Very large buildings (≈100 × 100 m, several floors) take a few seconds to generate.
- The ASCII renderer is a debug view; image rendering is the next step.
