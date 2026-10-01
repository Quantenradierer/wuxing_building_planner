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
| `building_type` | see "Building types"                     | honoured                              |
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
| data_centre | corridor        | server halls behind mantraps, NOC, UPS and cooling |
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
Casino (2026-10-01, hall): a gaming floor with slots along the walls, slot banks, card,
roulette and craps tables and a bar; high-roller salons; a sky lounge with restaurant tables
on the top floor of taller casinos. The money path is a unit (`cage_complex`): the cash
cage opens onto the floor, the count room lies behind it and the vault behind that (in deep
rows beside it); only the cage has a door out, the others have the tier's locks. The eye
in the sky (surveillance room) is on the first upper floor, else on the ground floor.
Small street shops on one pattern (2026-10-01): `gun_shop`, `talismonger`, `ramen_bar`,
`boutique`, `deli`. A hall building without a service corridor: the sales floor on the
street with its counter against a wall near the trade's back room, back rooms opening onto
it (staff WC, office, storage and the trade's own rooms from the `shops` room catalog),
stock rooms on middle floors, the owner's flat on the top floor (a loft as the floor's hall,
bedrooms and a bathroom behind it), stock and plant in the basement; rich gun shops have a
firing range there. The programs share their structure (keep them alike); the rooms are
shared through the catalogs (`common`, `residential`, `retail`, `shops`).
Variants of the apartment (2026-10-01): `safehouse`, `hacker_den` and `mage_flat`. Each is a
single dwelling, not a block with one special unit: the corridor layout with the flat's hall
as corridor and a small `entry_hall` as lobby, so the whole map is the flat and its special
rooms (gear room, bunk room, med room, panic room or stash; rig room and server closets;
library, ritual room (`lodge`), alchemy lab) are ordinary program rooms. A unit of the apartment
block holds only a hall, front rooms and one row of back rooms, too few and too small for
them. Bigger footprints become a bigger dwelling (more bedrooms, rig rooms, libraries).

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
  supermarkets and police stations need about 36 × 36 cells (18 m), data centres 36 × 36
  (the ground floor holds the NOC, security post, loading dock and a server hall with its
  mantrap), casinos 38 × 38, factories 40 × 40, hospitals and single-floor prisons 48 × 48,
  others 32 × 32 (single floor, any tier, security and street/service side). Prisons from
  34 × 34 only work for some sides and tiers (about 1 in 3 fails up to 40 × 40); small
  prisons drop the intake holding cells, the vehicle sally port, mess hall, kitchen and
  infirmary before their one cell block. Small street shops fit every tier from 24 × 24
  with stairs and a basement (boutique 16 × 16, gun shop 21 × 21, middle tier 19–22); one
  floor fits from 13 cells deep or wide (ramen bar 15). The single flats are smaller:
  safehouse 22 × 22 or 24 × 18 cells, hacker den 22 × 22 or 22 × 18, mage's flat 28 × 28
  or 30 × 22 (the lodge needs 3.5 m round its circle); at these sizes optional rooms (med
  room, second bedroom, alchemy lab) are often left out.
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

Requested by the author (2026-09-27), done (2026-10-01):
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
- Supermarkets with more than one floor have public stairs on the sales floor instead of
  a stairwell: 5 × 10 cells against the hall's back edge, open to it on the long side and
  one end (`place: hall` core entry, `open: 1`); the freight elevator stays in the back of
  house. The hall then needs 15 cells of depth instead of 12, so L, T and U footprints need
  arms 3 cells thicker (120 random 2–3 floor supermarkets: 16 rejected as too small for
  their shape, 8 before). One floor stays the default.
- Drone rooms are tidy: docks stand on one lattice (`grid`, 1 m apart, 0.5 m from the
  walls) and racks side by side along one wall (`perimeter`), in drone bays and rigger
  workshops; they were scattered. Bays in buildings (6–9 cells wide) hold 1–5 docks.
- The nightclub's DJ booth heads the hall: in the middle of the short wall farthest from the
  doors (`end`), facing an open dance floor 5 m deep that tables, sofas and bars keep clear
  of (`front_clear: 10`); before, it stood on any wall, often in a corner between sofas
  and shelves, with tables in front of it.
- Public toilets compared with hand-drawn ones (2026-10-01): their sinks stood one by one
  on random walls, against stall partitions and far from the door, 1–3 whatever the
  stalls, the bin in any corner. Now the sinks are one row on one wall nearest the
  entrance, never against a stall, one per two stalls (3 stalls: 2 sinks in ~85 %, 1 where
  the wall is short), with the bin at the row's end when a flank is free (~65 %). Shower
  and scrub rooms line their sinks up too. A lone toilet (`--room toilet`) had no stall
  doors; now it does.
- Every public toilet has a sink (about 4 000 toilets in 1 900 random buildings):
  the 4 × 4 rest between the entrance and two stall doors that left ~0.5 % without one
  fits a 2 × 1 sink. Toilets give up stalls until 16 cells are left for sinks
  (`rest_area`), or become a single WC.
- Cluster hallways end after their last door (2026-10-01): they ran on to the facade
  (clinic toilets and storage). The end joins a room beside it, which becomes irregular
  (an L round the hallway's end) — the first rooms that aren't rectangles or a toilet
  around its stalls. A hallway that no room needs (its rooms all open onto the corridor)
  goes entirely. Rooms may have alcoves and wings of at least 1.5 m beyond a main
  rectangle of their minimum size. 300 random buildings: of ~520 hallways that ran on,
  ~410 were cut back or removed; nearly all of the rest run on one or two rows.
- No corridor stub to the back door (2026-10-01): apartment and hotel ground floors ran a
  dead-end corridor through the service-side strip to it. Now a back room takes that slot,
  from the corridor to the facade, and the back door opens into it: the parcel drone bay
  in apartments, the staff room in hotels (the first ground-floor fill room in the
  program's `service_rooms`, not more than 1.5× its maximum area). Of 300 random
  buildings, 111 of 114 such ground floors got the back room; 3 squatter hotels with deep
  strips keep the stub. The coffin block keeps its stub (no fitting fill room).

- Waiting rooms beside the reception lobby (2026-10-01): they opened onto it only where
  they happened to end up beside it (60 % in clinics, 40 % in hospitals); toilets and
  security rooms `near: entrance` often stood between. A room whose `access` names the
  lobby now goes into a segment beside it, at the lobby end: 92 % of clinic and 88 % of
  hospital waiting rooms (80 random buildings). The police station's front office has a
  visitor WC (1.5 × 2 m) carved out of it, entered only from it (54 of 60 random
  stations); before, the public toilets were behind its locked door. No stall goes where
  an exterior door is planned.

- No door into the elevator car (2026-10-01): a storeroom walled in by the stairwell, the
  elevator, toilet stalls and the facade (clinic and corp lab upper floors, factories)
  opened into the elevator. Such a stranded room now opens onto the stairwell, at the end
  of the wall nearest its landing: of 905 random buildings, 7 rooms opened into an
  elevator before, none now.

- Rooms that belong together (2026-10-01): `next_to` partners were placed in the order of
  their size, so bigger rooms filled the partner's segment first, windowless rooms kept off
  its facade row and cluster members were never moved beside it. Now a placed room's
  partner is placed right after it, segments keep space for pending partners, the relation
  counts both ways and slots holding a partner are ordered beside it. 40 random buildings
  per type: police observation rooms beside an interview room 53 % → 83 %, sterilization
  beside the OR 5 % → 88 % (cosmetic clinics) and 20 % → 90 % (clinics), corp lab decon
  rooms 59 % → 91 %, guard rooms beside the security room 53 % → 69 %. Observation rooms
  only go on floors with an interview room (`requires:`). Supermarket ground floors have a
  counted stockroom (60–160 cells), by the loading bay: floors whose only stockroom was
  behind the core, away from the sales floor, 11 % → 8 % (of those left, some border it
  behind the public stairs or sit in a wing).

- Back doors into back rooms (2026-10-01): where no corridor reaches the service facade,
  the back door opened into whatever room stood there: in 300 random buildings (15 per
  type), a third of the offices and street docs, a quarter of the clinics, half of the corp
  labs and cosmetic clinics and some hospitals, nightclubs and churches had it in an office,
  exam, consultation, operating, recovery or meeting room, a VIP room or a toilet. Every program now reserves a back room at the door
  (only apartments, hotels and the coffin block did): the first room of the ground floor
  among its `service_rooms` (a storeroom, staff room, backstage, DocWagon bay), standing in
  for one counted room of that type. Clinics, corp labs and cosmetic clinics have a
  storeroom by the back door; a church's back door goes into the sacristy. 600 random
  buildings (30 per type): none left. Hotel staff rooms behind the back door are at most twice their
  minimum area (they ranged up to the maximum).

Storage share (2026-09-27): leftover space goes to the row's rooms, other fill rooms
(`priority: optional` fill rooms for leftovers only: studios, drone bays, vending rooms)
and neighbours before it becomes a storeroom; basements and hall back strips got real
rooms (mechanical rooms, labs, staff rooms, …) instead of `storage` as their fill; archives
are counted rooms. Generic storage (storage, archive, records room, linen room, closets)
over 2 000 random buildings, all types: 19.8 % → 2.3 % of the area outside circulation,
every type ≤ 5 % except apartments (9 %, a third of it flat closets).
`tests/test_storage.py` keeps the share below 5 %.

Known weaknesses:
- Squatter hotels with deep strips (about 1 in 20 hotels) still have a corridor stub to the
  service door: their staff room would be too big for the full-depth slot.
- About 1 in 10 cluster hallways still runs one or two rows (0.5–1 m) past its last
  door; a dozen in 300 random buildings run on further where the only rooms beside the
  end are at their size limit (toilets, electrical and security rooms).
- Waiting rooms that find no free segment beside the lobby (the core or a narrow
  segment beside it, a second hospital waiting room) still open onto the corridor:
  about 1 in 12 clinic and 1 in 7 hospital waiting rooms. Police front offices under
  about 90 cells (squatter tier) have no visitor WC.
- A room walled in by core rooms and toilet stalls with no stairwell beside it would still
  open into an elevator car (none in 905 random buildings).
- Rooms that belong together: about 1 in 6 police observation rooms and 1 in 8 sterilization
  rooms still end up away from their partner where its segment is full; a hospital OR has
  the scrub room on one side, so sterilization, ICU and recovery rooms compete for the
  other (ICU beside an OR in about 1 in 5 surgery floors). Partners across the service
  corridor of a hall (factory control room, stuffer shack cashier booth) never touch it.
- Parking garages: a storeroom behind the core walled in by the stairwell, the elevators and
  toilet stalls opens onto the stairwell across the stairs on about 1 in 6 ground floors
  (1 in 16 before the back rooms of 2026-10-01; the toilet now often goes to the core's
  other side).
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
- Casinos: the back-of-house rooms open straight onto the gaming floor (no staff
  corridor); the casino objects (card, roulette and craps tables, slot machines, cash cart,
  count table) have prompts but no sprites yet and are drawn with stand-ins (pool table,
  arcade machine, laundry cart, lab bench). About 1 in 250 multi-floor casinos keeps a
  1-cell-wide hallway end between two toilets (hard warning).
- Small street shops: the owner's flat is reached only through the shop (no own street
  door); in wide shops its loft (the floor's hall) is large and sparse; the staff WC grows
  with leftover space (up to ~60 cells at luxury); the firing range exists only as the
  basement's hall, so only with `floors_below` ≥ 1; the counter stands on a side wall when
  the back wall is full of doors.
- Basements are mostly mechanical rooms side by side (one laundry, staff room and
  storeroom); a garage level would be more realistic for big buildings.

- Data centres (2026-10-01): every server hall is entered only through its mantrap (639
  halls in 120 random buildings, all of them); halls are the first choice of every fill,
  but thin interior rows of deep floors become UPS and electrical rooms, so halls are
  about 30 % of the floor area. The loading dock has `windows: required` only to keep it
  on a facade (a `facade_door` room may otherwise land in an interior row and lose its
  roller door); about 7 in 100 docks then report a missing window.

Possible extensions: surroundings (street, yard, parking, fire escapes, roof), more themes
(corporate white, Barrens, print), a web UI with preview, more building types and variants
(school, motel, DocWagon station).

Prison (2026-10-01): cell blocks (`prison_block`) are stall rooms like coffin halls: cells
of 2 × 3 m (bunk, steel toilet-basin) along both long walls of a dayroom with bolted
tables, entered only through a full-depth `block_sallyport` (vestibule), a guard bubble
(`block_control`) beside it. The public lobby leads to the visiting room (booths with a
glass partition; visitors' door from the lobby, inmates' from the corridor); intake (vehicle
sally port, intake room, holding cells, property room) sits at the service side; mess hall
and kitchen, infirmary, gym, laundry and solitary (`segregation_unit`) are spread over the
floors, a taller prison's top floor is admin with the warden. Stall rows of a room entered
only through its host now keep the door to the host free instead of the corridor wall,
and a row never cuts a corner off an irregular room (the hotel snapshot changed: an
earlier attempt became valid). Known weaknesses: a block whose slot is narrower than the
block plus its sally port (rare) opens onto the corridor directly; mess hall and
kitchen are not always neighbours (`next_to` works within a strip segment only); the yard
is not modelled (needs surroundings), the gym stands in for it.
