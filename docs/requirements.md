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
  and VTT exports would use it. The unused `cover` field on objects was removed (2026-09-27).
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
| `building_type` | see "Building types" (14 types)                     | honoured                              |
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

## Building types

| Type        | Layout strategy | Notes                                               |
|-------------|-----------------|-----------------------------------------------------|
| office      | corridor        | lobby/reception, offices, open plan, meeting rooms  |
| clinic      | corridor        | many specialised rooms, surgery floor, wards        |
| apartment   | corridor+units  | flats along a corridor, each subdivided             |
| supermarket | hall            | sales hall per floor, back-of-house strip, loading  |
| hospital    | corridor        | ER, imaging, ICU, surgery floor, wards, morgue      |
| hotel       | corridor+units  | rooms with bath; capsule hotel at squatter / low    |
| corp_lab    | corridor        | wet labs, clean rooms, test chambers, server room   |
| nightclub   | hall            | dance floor / lounge hall, VIP rooms, backstage     |
| warehouse   | hall            | racked hall, loading bays, shipping office          |
| corp_office | corridor        | checkpoint lobby, cubicle floors, executive top     |
| street_doc  | corridor        | cover storefront, back-room OR, recovery rooms      |
| cosmetic_clinic | corridor    | no ER: consultations, OP, cyberware fitting, suites |
| coffin_block| corridor        | tiny coffin units, shared toilets and showers       |
| stuffer_shack | hall          | convenience store: small sales floor, stock room    |
| police_station | corridor     | squad rooms, interview + observation, cell block    |
| parking_garage | hall         | parking decks with car rows, ramps lined up         |
| factory     | hall            | production hall (conveyors, machines), control room |
| church      | hall            | nave with pews and altar, choir loft, crypt         |
| dive_bar    | hall            | taproom: bar with stools, booths, pool table        |
| chop_shop   | hall            | workshop with car lifts, paint booth, parts storage |

## Building rules

- Rules are data: YAML files validated with Pydantic.
  - A shared **room catalog** (toilet, storage, stairwell, corridor, …) with area ranges,
    minimum side, window requirement, door width, which rooms it is entered from
    (`access`), whether it may be walked through (`transit`), its own vehicle door
    (`facade_door`), wealth overrides, furniture.
  - One **program** per building type: layout strategy, corridor and strip sizes, facade
    grid, vertical core, entrances, units, floor roles (matched by ground / upper / top /
    basement plus `when:` and `wealth:`) and room entries with `count`, `share`, `fill` or
    `place`, a priority (`required`, `normal`, `optional`) and `near: core | entrance`.
  - `when:` conditions are a deliberately tiny expression language (floors, level and the
    building's `width` and `depth`: bigger buildings get more elevators).
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
- Vehicle and delivery bays (`facade_door`) have their own wide exterior door; the
  service door goes into such a bay if it faces the service side.

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
  accents per room type), swappable themes (a textured asset pack is just another theme;
  `neon_sprites` draws objects with painted Midjourney sprites).
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
Later: police station, parking garage, factory and church, plus the variants dive bar
(`dive_bar`, of the nightclub) and chop shop (`chop_shop`, of the warehouse).

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

- No fire escapes (they need surroundings).
- Adjacency preferences are limited to `near`, `next_to`, `vestibule` and door `access` lists.
- Small footprints can be infeasible for rich tiers or big programs: cosmetic clinics,
  supermarkets and police stations need about 36 × 36 cells (18 m), factories 40 × 40,
  hospitals 48 × 48, others 32 × 32.
- Very large buildings (≈100 × 100 m, several floors) take a few seconds to generate.
- The ASCII renderer is a debug view.
- Universal VTT has no window type: windows are exported as (closed) portals, i.e. doors.
- Foundry export needs the import macro (scenes keep their ids so the stairs stay linked).

All v2 roadmap steps are implemented (2026-09-26).

## Open points (2026-09-27)

To verify:
- VTT exports in a real Foundry (doors, windows, lights, stair/elevator teleports) and a
  Universal VTT viewer; so far only checked against the format descriptions.
- Re-render examples once the sprite set for the newer objects exists (desk/table kinds,
  church, garage and factory objects; Midjourney run from 2026-09-28).
- 44 object kinds have a prompt in `tools/sprite_prompts.yaml` but no pick yet and are drawn
  with stand-ins: the desk/table kinds (huddle_table included), condenser, satellite_dish, church and factory objects, `round_table`, and the
  Sixth World objects (electrical_panel, battery_bank, hvac_unit, generator, fuel_tank,
  suppression_tanks, fire_extinguisher, rigger_cocoon, drone_dock, drone_rack,
  deposit_boxes, sim_rig, autokitchen, mattress, fire_barrel, cyberware_rack, cryo_pod,
  ambulance, evidence_locker, shipping_container). Needs a Midjourney round.

Decided by the author (2026-09-27):
- Sprite wealth fallback steps towards middle (luxury → high → middle, squatter → low →
  middle); it is only the fallback.
- `wealth_shift` of rooms stays relative to the building (executive office +1); revisit
  only if it gets in the way.
- No stalls in shower rooms and locker rooms for now.
- Tactical layer: the unused `cover` field was removed from objects.

Requested by the author (2026-09-27), still open:
- Drone rooms need some order (docks and racks placed in a tidy arrangement).
- Supermarkets rarely have a second floor; when they do, the stairs are public stairs on
  the sales floor, not in a separate stairwell.
- Nightclubs need a DJ table.

Done from that list (2026-10-01):
- Sinks are 2 × 1 cells again (1 × 0.5 m; they were 2 × 2). Kitchens get their optional
  sink about half the time (60 apartment/office/clinic/hotel/hospital kitchens: 14 → 29);
  the square sprites are stretched until 2:1 ones are rendered.
- Urinals are gone (object kind, sprites and the `line`/`alternate` rule options
  that only served them); public toilets have stalls and sinks.
- Focus rooms are booths of 16–30 cells (mostly 4 × 6, 2 × 3 m); they were up to 48.
- Coffin units are pods of 2 × 5 cells (1 × 2.5 m; a row's last one 3 × 5) along both
  sides of a coffin hall's aisle, hatches opening out; they were 4 × 4 to 4 × 7 rooms.
  Small rooms (pods, toilet stalls, holding cells) are laid out from `templates:` (size,
  door, objects) instead of being furnished by rules. Door clearances are as deep as the
  door is wide only on the side the leaf swings into, one cell on the other.

Storage share (2026-09-27): leftover space goes to the row's rooms, other fill rooms
(`priority: optional` fill rooms for leftovers only: studios, drone bays, vending rooms)
and neighbours before it becomes a storeroom; basements and hall back strips got real
rooms (mechanical rooms, labs, staff rooms, …) instead of `storage` as their fill; archives
are counted rooms. Generic storage (storage, archive, records room, linen room, closets)
over 2 000 random buildings, all types: 19.8 % → 2.3 % of the area outside circulation,
every type ≤ 5 % except apartments (9 %, a third of it flat closets).
`tests/test_storage.py` keeps the share below 5 %.

Known weaknesses:
- Toilets: about 0.5 % of public toilets still have no sink (a 4 × 4 rest between the
  entrance and two stall doors). The others give up stalls until 16 cells are left for
  sinks (`rest_area`), or become a single WC; before, ~10 % had no sink.
- Cluster hallways run on to the facade past their last door (clinic toilets and storage),
  and apartment and hotel floors have a corridor stub to the service door.
- Waiting rooms open onto the reception lobby only when they end up beside it (about 60 %
  in clinics and hospitals); the others open onto the corridor. The police station's
  public area is just the front office: public toilets are behind its locked door.
- A storeroom behind the elevator, walled in by the stairwell and toilet stalls, can open
  into the elevator (3 of ~1000 rooms beside cores, supermarket upper floors).
- Rooms that belong together: about 60 % of police observation rooms sit beside an
  interview room (`next_to` only orders rooms within one strip segment); a supermarket
  stockroom borders the sales floor in about half the cases (the others sit behind a
  cluster hallway).
- Without a corridor or back room on the service facade the plain service door opens into
  whatever room is there (an exam room or storeroom in small clinics).
- Vehicle access: every bay with `facade_door` (loading bay, DocWagon bay, sally port,
  garage, warehouse, factory and chop shop halls) gets its own roller door, and such rooms
  never go into clusters or behind the core, where they would have no facade. Still open:
  a hall's roller door can end up on a side facade; the police station's garage exists
  only in the basement, without a ramp.
- Hall buildings (supermarket, warehouse, factory, nightclub, dive bar, parking garage)
  have one thin back-of-house strip; it is full before the optional Sixth World rooms
  (electrical, generator, fixer's room, cashier booth at low wealth) get a turn, so they
  mostly appear only in bigger footprints or basements. Spider stations and drones there
  are furniture instead (rigger cocoon in the security room, drone docks in the hall).
- Hospital 70 × 44 × 3: the top floor's doctor's office can reach 171–190 cells (limit
  105), 1 of 12 seeds.
- Hotel guest rooms narrower than hall plus guest room (8 cells) keep the small hallway
  between corridor, bathroom and guest room.
- About 50 of ~33 000 rooms (400 random buildings, all types and tiers) exceed 1.5x their
  catalog maximum, spread over single cases (corp lobby, exam room, airlock, office).
  Absorbing leftovers raised this by ~15 % (2 000 buildings: 621 → 719 rooms), mostly
  coffin units and rooms beside thin slivers.
- Elevator doors are drawn and exported as hinged doors swinging into the car; they should
  slide.
- Leftover storerooms can open into the lobby or lounge instead of a corridor (hotel
  56 × 36: storeroom beside the lounge).
- Apartment halls run the whole unit width in deep strips (up to ~100 cells); every back
  room must touch them, so a unit with four back rooms needs the whole spine. Shortening
  it to the doors saved only ~5 % and cut closets off; smaller units would help more.
- Small apartment buildings (one or two segments per floor) still keep leftover
  storerooms behind small service rooms; flat closets (`front_fill`) are ~3 % of the area.
- Basements are mostly mechanical rooms side by side (one laundry, staff room and
  storeroom); a garage level would be more realistic for big buildings.

Possible extensions: surroundings (street, yard, parking, fire escapes, roof), more themes
(corporate white, Barrens, print), a web UI with preview, more building types and variants
(school, motel, DocWagon station).

