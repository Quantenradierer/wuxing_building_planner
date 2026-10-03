# Requirements

Roomplanner generates battle maps of building interiors for Shadowrun / cyberpunk
tabletop games. Output is a structured model (versioned JSON), a minimal ASCII debug view,
themed images and VTT exports; a local web UI previews buildings.

## Scope

In scope:

- Interior of a single building. No lot, street or neighbouring buildings.
- Structure: exterior and interior walls, doors, windows, stairs/elevators.
- Room semantics: every room has a type and a label.
- Furniture and fixtures.
- Multiple floors above ground and basements.
- Security layer: devices (cameras, maglocks, card readers, alarm panels), security rooms
  per tier, door properties (lock, rating, material).
- Condition layer: debris, removed furniture, broken / missing / blocked doors, wall
  breaches, collapsed areas, broken windows; decals in the image renderer.
- Lights: per room type, altered by condition and security.
- Image rendering (procedural, themeable) and VTT export (Universal VTT, Foundry VTT).

Later (the model must allow them without redesign):

- Tactical markup layer (cover, difficult terrain): postponed until it is clear how images
  and VTT exports would use it.
- Surroundings (lot, parking, loading area, street, yard, roof, fire escapes). The canvas
  may then be larger than the footprint; cells outside the footprint are the exterior zone.
- Basement garages and garage levels for big buildings.
- Roll20 export.
- More themes (corporate white, Barrens, print) and building types (school, motel,
  DocWagon station).

Out of scope: objective markers (paydata, safes, …), scenario design.

## Grid

- 1 cell = 0.5 m × 0.5 m. A classic battle square (1.5 m) is 3×3 cells.
- Walls, doors and windows lie on **edges between cells**, never in cells.
- **All units are cells**: parameters, rule files and output use cells for lengths and
  cell counts for areas. Meters appear only as the scale hint `cell_size_m` in the JSON.

## Parameters

| Parameter       | Values                                              | Effect                                |
|-----------------|-----------------------------------------------------|---------------------------------------|
| `building_type` | see "Building types"                                | room program and layout strategy      |
| `width`         | cells (east–west)                                   | bounding box for non-rectangles       |
| `depth`         | cells (north–south)                                 | bounding box for non-rectangles       |
| `floors_above`  | ≥ 1, includes the ground floor                      |                                       |
| `floors_below`  | ≥ 0 (basements)                                     |                                       |
| `wealth`        | squatter, low, middle, high, luxury                 | sizes, program, furniture             |
| `condition`     | pristine, maintained, run_down, derelict, ruined    | condition layer, sprite look          |
| `security`      | none, low, corporate, aaa                           | security layer                        |
| `shape`         | rectangle, l, u, t, z, stepped, irregular           | `irregular` picks one by seed         |
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
| data_centre | corridor        | server halls behind mantraps, NOC, UPS and cooling  |
| casino      | hall            | gaming floor, high-roller salons, cage → count room → vault |
| gun_shop    | hall            | cases, wall racks, strongroom; firing range (rich)  |
| talismonger | hall            | reagent shelves, ritual room with hermetic circle   |
| ramen_bar   | hall            | counter seats round an open kitchen, cold room      |
| boutique    | hall            | clothes racks, changing booths, mirrors, tailor     |
| deli        | hall            | meat and cheese counter, butchery, cold room        |
| safehouse   | corridor        | one flat: bunks, gear room, med room, panic room / stash, back door |
| hacker_den  | corridor        | one flat, mostly rig room; server closets, a bed    |
| mage_flat   | corridor        | one flat: hermetic library, ritual room, alchemy lab |
| prison      | corridor        | cell blocks behind sally ports, intake, visiting    |

Variants of another type (corp_office, street_doc, coffin_block, stuffer_shack, dive_bar,
chop_shop, the shops and the flats) are separate types with their own YAML.

- **Casino**: a gaming floor with slots along the walls, slot banks, card, roulette and
  craps tables and a bar; high-roller salons; a sky lounge with restaurant tables on the
  top floor of taller casinos. The money path is a unit (`cage_complex`): the cash cage
  opens onto the floor, the count room lies behind it and the vault behind that (in deep
  rows beside it); only the cage has a door out, the others have the tier's locks. The eye
  in the sky (surveillance room) is on the first upper floor, else on the ground floor.
- **Small street shops** (`gun_shop`, `talismonger`, `ramen_bar`, `boutique`, `deli`): a
  hall building without a service corridor. The sales floor is on the street with its
  counter against a wall near the trade's back room; back rooms open onto it (staff WC,
  office, storage and the trade's own rooms from the `shops` catalog); stock rooms on middle
  floors; the owner's flat on the top floor (a loft as the floor's hall, bedrooms and a
  bathroom behind it); stock and plant in the basement; rich gun shops have a firing range
  there. The programs share their structure (keep them alike); rooms are shared through
  the catalogs (`common`, `residential`, `retail`, `shops`).
- **Single flats** (`safehouse`, `hacker_den`, `mage_flat`): one dwelling, not a block
  with one special unit. The corridor layout uses the flat's hall as corridor and a small
  `entry_hall` as lobby, so the whole map is the flat and its special rooms are ordinary
  program rooms (an apartment unit holds too few and too small rooms for them). Bigger
  footprints become a bigger dwelling.
- **Prison**: cell blocks (`prison_block`) are stall rooms like coffin halls: cells of
  2 × 3 m (bunk, steel toilet-basin) along both long walls of a dayroom with bolted tables,
  entered only through a full-depth `block_sallyport` (vestibule), a guard bubble
  (`block_control`) beside it. The public lobby leads to the visiting room (booths with a
  glass partition; visitors' door from the lobby, inmates' from the corridor); intake
  (vehicle sally port, intake room, holding cells, property room) is at the service side;
  mess hall, kitchen, infirmary, gym, laundry and solitary (`segregation_unit`) are spread
  over the floors; a taller prison's top floor is admin with the warden.
- **Data centre**: every server hall is entered only through its mantrap; halls are the
  first choice of every fill, thin interior rows become UPS and electrical rooms.

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
- Multi-floor supermarkets have public stairs on the sales floor (`place: hall` core entry,
  open to the hall on the long side and one end) instead of a stairwell; the freight
  elevator stays in the back of house.

## Entrances

- The building type defines which entrance kinds exist and their widths: `main`, `service`
  (loading dock, ambulance bay, back door), `emergency` (exit from a corridor end or the
  stairwell; a second stairwell has its own) and `roof` (hatch or door from the top floor's
  stairwell; the roof itself is not modelled). The `entrances` parameter overrides the type's list.
- The main entrance faces `street_side`; the service entrance faces `service_side`.
- Vehicle and delivery bays (`facade_door`) have their own wide exterior door; they never
  go into clusters or behind the core. The service door goes into such a bay if it faces
  the service side.
- Every program reserves a back room at the back door (the first ground-floor room among
  its `service_rooms`: storeroom, staff room, backstage, sacristy, DocWagon bay, parcel
  drone bay), so it never opens into an office, toilet or a dead-end corridor stub.

## Openings

- An opening is a door or a window: a contiguous, straight run of wall edges.
- Doors carry a swing: the side they open towards and the hinge end. Doors open into
  rooms; exits open outwards.
- Door clearances are as deep as the door is wide on the side the leaf swings into, one
  cell on the other.
- No room opens into an elevator car; a room walled in by the core opens onto the
  stairwell, at the end of the wall nearest its landing.

## Room layout

- Leftover space goes to the row's rooms, other fill rooms (`priority: optional` fill rooms
  for leftovers only) and neighbours before it becomes a storeroom. Generic storage stays
  below 5 % of the area outside circulation (`tests/test_storage.py`).
- Rooms may be irregular: alcoves and wings of at least 1.5 m beyond a main rectangle of
  their minimum size (e.g. an L round the end of a cluster hallway). Cluster hallways end
  after their last door; a hallway no room needs is removed.
- `next_to` partners are placed right after each other, segments keep space for pending
  partners and the relation counts both ways. `requires:` keeps a room off floors without
  its partner (observation rooms need an interview room).
- A room whose `access` names the lobby goes into a segment beside it, at the lobby end
  (waiting rooms).
- Small rooms (coffin pods, toilet stalls, holding cells, prison cells) are laid out from
  `templates:` (size, door, objects) instead of being furnished by rules. Stall rows of a
  room entered only through its host keep the door to the host free and never cut a corner
  off an irregular room.

## Furnishing

- Public toilets have stalls and one row of sinks on the wall nearest the entrance, never
  against a stall, one per two stalls, the bin at the row's end when a flank is free.
  Toilets give up stalls until 16 cells are left for sinks (`rest_area`), or become a
  single WC. Shower and scrub rooms line their sinks up too. No urinals; no stalls in
  shower and locker rooms.
- Sinks are 2 × 1 cells; kitchens get their optional sink about half the time.
- Focus rooms are booths of 16–30 cells. Coffin units are pods of 2 × 5 cells along both
  sides of a coffin hall's aisle, hatches opening out.
- Drone docks stand on one lattice (`grid`), racks side by side along one wall
  (`perimeter`).
- The nightclub's DJ booth stands in the middle of the short wall farthest from the doors
  (`end`), facing a clear dance floor 5 m deep (`front_clear: 10`).
- The police station's front office has a visitor WC carved out of it.

## Wealth and condition

Wealth affects room sizes, the room program (rooms restricted to tiers), furniture density
and variety, and corridor widths. Driven by global per-tier multipliers in the catalog, which
rooms and program entries may override or restrict. With fixed building dimensions, bigger
rooms mean fewer rooms. A room's `wealth_shift` is relative to the building (executive
office +1).

Condition moves the sprite look: pristine one tier up (middle..luxury), maintained as is
(low..high), run down one down (squatter..middle, no rubble), derelict and ruined two down
(squatter..low, with rubble). Sprite lookup falls back towards middle (luxury → high →
middle, squatter → low → middle), then to the theme's stand-in.

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
- CLI (Typer): generate to ASCII, JSON, PNG/WebP, Universal VTT or Foundry; re-render a
  saved JSON; `ui` starts the local web UI.
- The JSON is a versioned contract (`schema_version`) for renderers and exporters.
- The ASCII renderer is a minimal debug view and will not be polished.

## Rendering and VTT export

- The image renderer reads only the JSON. Procedural cyberpunk style (dark concrete, neon
  accents per room type), swappable themes; sprite themes draw objects and floor textures
  with painted sprites (`docs/sprites.md`). PNG or WebP, one image per floor, default
  50 px per cell. Lights appear as a subtle glow; the VTT does the real lighting.
- VTT export: Universal VTT (`.dd2vtt`, one file per floor) and Foundry VTT (v14+, one
  scene with a level per floor, imported with the Schattenakte module). Grid size is a per-VTT parameter,
  default 1 m (2 × 2 cells). Walls, doors (locked if they have a lock), windows (block
  movement, not sight or light) and lights are exported; lights can be switched off.
  Foundry stairs and elevators change levels via native Scene Regions.

## Quality

- Unit tests, snapshot tests (fixed params and seed), property-based tests (Hypothesis)
  that assert the validator's hard invariants for arbitrary valid parameters.
- Tooling: uv, pytest, Hypothesis, ruff, pyright, Python 3.14.

## Status

All v1 milestones and v2 roadmap steps (shapes, layout fixes, image renderer, security,
condition, lights, more entrances, new building types, VTT export) are implemented. The
history is in git. What is left is under "Open issues".

## Open issues

Building code (measured in "Building code" below): the soft warnings and their pass-rate
sweep (`tests/test_buildingcode.py`) are done; further rules are listed there.

To verify:
- VTT exports in a real Foundry (Schattenakte import, levels, doors, windows, lights,
  stair/elevator level changes) and a
  Universal VTT viewer; so far only checked against the format descriptions.
- Object kinds without a sprite are drawn with stand-ins (`sprite_fallbacks`); open sprite
  work is listed in `docs/sprites.md`. Re-render the examples once the sets are complete.

Footprints:
- Small footprints can be infeasible for rich tiers or big programs. Minimum footprints
  (single floor, any tier, security and street/service side): 32 × 32 cells in general;
  cosmetic clinics, supermarkets, police stations and data centres 36 × 36; casinos
  38 × 38; factories 40 × 40; hospitals and single-floor prisons 48 × 48. Prisons from
  34 × 34 work only for some sides and tiers (about 1 in 3 fails up to 40 × 40); small
  prisons drop intake holding cells, vehicle sally port, mess hall, kitchen and infirmary
  before their one cell block. Small street shops fit every tier from 24 × 24 with stairs
  and a basement (boutique 16 × 16, gun shop 21 × 21, middle tier 19–22); one floor fits
  from 13 cells deep or wide (ramen bar 15). Single flats: safehouse 22 × 22 or 24 × 18,
  hacker den 22 × 22 or 22 × 18, mage's flat 28 × 28 or 30 × 22 (the lodge needs 3.5 m
  round its circle); at these sizes optional rooms are often left out.
- Multi-floor supermarkets need arms 3 cells thicker for L, T and U shapes (the public
  stairs need a 15-cell-deep hall).
- Very large buildings (≈100 × 100 m, several floors) take a few seconds to generate.

Layout:
- Adjacency preferences are limited to `near`, `next_to`, `vestibule` and door `access`
  lists. `next_to` works within a strip segment only: about 1 in 6 police observation
  rooms and 1 in 8 sterilization rooms end up away from their partner; a hospital OR has
  the scrub room on one side, so sterilization, ICU and recovery compete for the other
  (ICU beside an OR on about 1 in 5 surgery floors); partners across a hall's service
  corridor (factory control room, stuffer shack cashier booth, prison mess hall and
  kitchen) never touch.
- Waiting rooms that find no free segment beside the lobby open onto the corridor (about
  1 in 12 clinic and 1 in 7 hospital waiting rooms). Police front offices under about
  90 cells (squatter) have no visitor WC.
- About 1 in 10 cluster hallways runs one or two rows past its last door; a few run on
  further where the rooms beside the end are at their size limit.
- Squatter hotels with deep strips (about 1 in 20 hotels) and the coffin block keep a
  corridor stub to the service door (no fitting back room).
- A room walled in by core rooms and toilet stalls with no stairwell beside it would open
  into an elevator car (none seen). Parking garages: a storeroom behind the core opens onto
  the stairwell across the stairs on about 1 in 6 ground floors.
- Leftover storerooms can open into the lobby or lounge instead of a corridor (hotel
  56 × 36). Small apartment buildings keep leftover storerooms behind small service rooms;
  flat closets (`front_fill`) are ~3 % of the area.
- About 50 of ~33 000 rooms exceed 1.5× their catalog maximum (corp lobby, exam room,
  airlock, office, coffin units, rooms beside thin slivers). Hospital 70 × 44 × 3: the top
  floor's doctor's office can reach 171–190 cells (limit 105), 1 of 12 seeds.
- Apartment halls run the whole unit width in deep strips (up to ~100 cells); every back
  room must touch them. Smaller units would help.
- Hotel guest rooms narrower than 8 cells keep the small hallway between corridor,
  bathroom and guest room.
- Hall buildings have one thin back-of-house strip; it is full before the optional Sixth
  World rooms (electrical, generator, fixer's room, cashier booth) get a turn, so they
  mostly appear only in bigger footprints or basements. Spider stations and drones there
  are furniture instead.
- Basements are mostly mechanical rooms side by side.
- A hall's roller door can end up on a side facade; the police station's garage exists only
  in the basement, without a ramp.
- No fire escapes and no prison yard (they need surroundings; the gym stands in for the
  yard).

Building types:
- Casinos: back-of-house rooms open straight onto the gaming floor (no staff corridor);
  about 1 in 250 multi-floor casinos keeps a 1-cell-wide hallway end between two toilets
  (hard warning).
- Small street shops: the owner's flat is reached only through the shop; in wide shops its
  loft is large and sparse; the staff WC grows with leftover space (up to ~60 cells at
  luxury); the firing range is the basement's hall, so only with `floors_below` ≥ 1; the
  counter stands on a side wall when the back wall is full of doors.
- Prisons: a block whose slot is narrower than the block plus its sally port (rare) opens
  onto the corridor directly.
- Data centres: the loading dock has `windows: required` only to keep it on a facade;
  about 7 in 100 docks report a missing window.

Rendering and export:
- Universal VTT has no window type: windows are exported as (closed) portals, i.e. doors.
- Foundry: v14+ only (Scene Levels); the Schattenakte module must be published (GitHub
  release, `.github/workflows/schattenakte.yml`) before others can install it by URL.

## Building code

`tools/codecheck.py` generates a fixed sample of 100 buildings (all 31 types, random size,
floors, wealth, security and shape; 296 floors) and counts how many pass German building
rules (`uv run python tools/codecheck.py [count] [--json out.json]`; the docstring says how
each rule is measured). Results (2026-10-03):

| Rule (source)                                              | Pass                      |
|------------------------------------------------------------|---------------------------|
| Escape distance ≤ 35 m to a stairwell or exit (MBO §35)    | 86 / 100                  |
| Two escape routes, strict (MBO §33)                        | 55 / 100                  |
| Two escape routes, rescue windows on floors 1–7 count      | 98 / 100                  |
| Dead-end corridor ≤ 15 m                                   | 66 / 100                  |
| Corridor ≥ 1.0 m (ASR A2.3) / ≥ 1.5 m (DIN 18040)          | 100 / 100                 |
| Doors ≥ 0.9 m (DIN 18100)                                  | 41 / 100; 100 except stalls |
| 1.5 × 1.5 m turning space at doors (DIN 18040)             | 0 / 100 (29 % of door sides fail) |
| Stairwell 2.5 × 4.5 m (DIN 18065, 3 m floors)              | 81 / 81                   |
| Lift car 1.1 × 1.4 m (DIN EN 81-70)                        | 100 / 100                 |
| Window area ≥ 1/8 of floor area (MBO §47)                  | 56 / 65 (16 of 803 rooms) |
| 8 m² + 6 m² per further workstation (ASR A1.2)             | 45 / 64 (33 of 496 rooms) |
| Parking stall ≥ 2.3 m, lane 6.5 m (5.5 m at 2.5 m, MGarVO) | 3 / 3                     |

Done: second stairwell. Big floors (`width + depth >= 90`, 18 programs) get a second
stairwell (`place: far` core entry) at the part end farthest from the core, at least 12 m
from it, with its own exit on the ground floor: two routes 19 → 55, escape distance 79 → 86,
dead ends 51 → 66. The rest: floors under the threshold with one stair (rescue windows
cover them) and very big floors (a 71 × 56 police station still has 52 m); long wings may
need a stairwell of their own.
Cost: in 200 sample buildings 12 more rooms are dropped (135 → 147), mostly ground-floor
back rooms of factories, whose one back-of-house strip now also holds the second stairwell.

Done: stairwell size. Small programs ask for 5 × 9 cells (was 5 × 8); the catalog minimum is
45 cells and does not shrink at squatter and low tier, so a stairwell is only shortened to a
shallow strip while it keeps 4.5 m (else it turns across the strip).

Done: window area. Rooms that need windows widen their grid windows edge by edge (an edge
of wall left between windows and beside doors) or get one off the grid until they have a
window cell per 24 floor cells (`DAYLIGHT`, `pipeline/openings.py`); 57 failing rooms →
17. The rest: rooms without any facade (a ward, a bedroom, data-centre loading docks; the
validator warns), and deep rooms whose facade is glazed but for the piers (church naves
and choir lofts, 8.5 m deep offices, a corp penthouse) — those need shallower rooms.

Done: parking. Cars stand 5 cells apart (2.5 m stalls) in rows that all face a drive lane
(`rows` with `gap: 1`, `lanes: true`): row, 5.5 m lane, two rows back to back, lane, ...
(MGarVO allows 5.5 m lanes at 2.5 m stalls, 6.5 m only at 2.3 m); `front_clear` keeps
pay stations and drone docks out of the lanes. 8–30 % fewer cars per deck (most on small
decks, where one row and its lane fill a 10 m deep deck).

Done: desk density. Offices have a desk per 10 m² (`per: 40, scale: false`, so richer
offices are bigger, not denser), open offices and squad rooms blocks of four desks with
2 m aisles, consoles and shipping desks one per 10 m²; manager offices are at least 8 m².

Not pursued: one-desk rooms under 8 m² (focus booths, security rooms, guard bubbles,
parking attendant booths: all the remaining workstation failures), turning space (half the
failures are coffin pods and WC stalls, the rest furniture; DIN 18040 only applies to
accessible routes) and 1-cell stall doors (0.5 m; a 2-cell door does not fit a 2-wide
stall template). Not checked: toilet counts (ASR A4.1,
needs a headcount) and furniture clearances (DIN 18011).
