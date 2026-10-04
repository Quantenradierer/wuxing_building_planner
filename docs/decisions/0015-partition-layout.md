# 0015: Partition layout: one big room, cut into smaller ones

Status: accepted (2026-10-04); in progress on branch `partition-layout`, office only so far

## Context

The corridor layout (ADR 0003, architecture "Corridor layout") fixes the shape first: bands
of corridors and strips, rooms as slots in a strip. Room types are then picked to fill the
slots, and what no type fits becomes storerooms or is absorbed by neighbours. That keeps the
rooms regular, but a lot of tuning goes into avoiding leftovers: measured honestly (storage
and filler rooms, plus space merged into neighbours, `tools/leftover_metric.py`) about 5-7 %
of an office floor is leftover.

## Decision

A second, opt-in layout strategy `layout: partition`. It works the other way round: the floor
is one big room that gets cut, and room types are assigned to the pieces.

- Footprint = the big room, of any shape. Corridors are cut out first: as many parallel ones
  as keep each row of rooms within `strip_depth`, joined by cross corridors; a fallback adds
  side corridors to the outer wall for cells still too far from circulation (wings of L/U/T
  shapes). The lobby is a rectangle on the street facade whose inner side lies on a corridor.
  The core (stairs, lifts) is cut out beside a corridor.
- What remains splits into regions. Per floor role (floors of one role share it), the rooms
  the role needs (`count`, `share`, required first, the biggest size class first) each take
  the free region that fits best or a piece cut from a larger one. Cuts are straight, two
  levels deep, so pieces reach into corners; sometimes a corner rectangle is carved so the
  rest is an L.
- The rest of the regions are typed from the role's `fill` rooms: the type furthest below
  its share of the mix (`weight`) plus noise, among those that fit the region; a region too
  big for any type is cut further, a piece nothing fits becomes a leftover and is absorbed.
- Size classes (tiny / small / medium / large / huge, `data/sizes.yaml`) are derived from a
  room's `area` range; a room fits every class its range overlaps.
- Windows and `near:` are soft scores. A piece must touch circulation for a door and must not
  leave a pocket nobody can enter (blind cells behind it count against a cut).
- Floor roles are reused unchanged; the strategy reads them like the corridor layout.

## Consequences

- Rooms are irregular where cores, corridors and corner carves meet; the old layout's
  special features (annexes, vestibules, `next_to`, balconies, roofs' balconies, units,
  service stubs) are not ported yet. Programs with `units` are rejected by the feasibility
  check. Buildings opt in per YAML; the corridor and hall layouts stay.
- Stalls are carved by the existing `carve_stalls`, leftovers absorbed by the existing
  `absorb_leftovers`.
- `tools/leftover_metric.py` compares layouts; the property tests cover whichever layout a
  program names.
