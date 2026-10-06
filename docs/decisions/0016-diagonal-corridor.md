# 0016: A diagonal corridor between rooms, with diagonal doors

Status: accepted (2026-10-06)

## Context

ADR 0014 made 45° walls possible, but only as chamfered building corners: the cut triangle is
open air, nothing opens in it. A diagonal corridor needs walls between two rooms at 45° and
doors in them.

## Decision

- **A diagonal corridor is first a staircase.** The partition layout (`corridor.diagonal`, the
  program's chance, office 0.4) adds one band of corridor cells between two parallel corridors:
  a 45° path across free floor, thickened to the corridor's width less one. It flanks rooms on
  both sides (free floor a few cells either side) and keeps clear of other corridors. Layout,
  assignment and the cell model see an ordinary, jagged corridor. Its own random stream
  (`rng("diagonal")`), so buildings without one stay as they were.
- **Bevels turn the steps into walls.** After the rooms are typed, `_bevels` cuts the room
  cells at the staircase corners (corridor on two adjacent sides and between them, none on
  the opposite two) with a `Diagonal` whose cut triangle goes to the corridor: the cell stays
  in its room, the triangle is the corridor's. `FloorPlan.diagonals` holds them per floor.
  Circulation, core and annex cells stay square.
- **Inner diagonals.** A `Diagonal` whose two cut edges are not exterior is a wall between
  two rooms (`Floor.is_inner_diagonal`): the cells across both cut edges belong to one other
  room, the validator checks that. The cut edges stay in `Floor.walls` (hidden, as for
  chamfers) so every room border is still a wall. Nothing else in the model changes.
- **Diagonal doors** are `Opening(diagonal=True)`: `edges` are, for each diagonal cell the
  door spans (the room's `door_width`), the cell's horizontal cut edge. They join the cell's
  room to the triangle's room, need not be contiguous, and go through the existing graph
  code (reachability, clearances, locks, states) unchanged. `Floor.diagonal_door_line` gives
  the slanted run for drawing and export. Door candidates are the runs of neighbouring
  diagonal cells on one line (`_diagonal_runs`); a room prefers its diagonal wall for its
  door into circulation over a straight one.
- Renderers: ASCII shows the door glyph in the diagonal's cell; the image draws interior
  diagonals as interior walls (not trimmed to the open air), the door's jambs, leaf and arc
  square to the wall; Universal VTT and Foundry export the diagonal wall and a portal / door
  along it, the wall left out where the door is. JSON: optional `diagonal: true` on openings,
  no schema bump.

## Consequences

- Rooms beside the corridor are wedges; the existing region cutting, `_merge_thin` and
  absorbing handle them, but some are odd shapes (wedge nap rooms, huddle rooms).
- Objects keep out of the diagonal cells (half floor), so a row of unused cells follows the
  wall.
- Windows and exterior doors never go in diagonals; a diagonal door's clearance is the
  rectangle in front of its cut edges, not a slanted one.
- Only the partition layout makes bands. Slanted corridors at other angles, curved walls and
  diagonal windows are still out.
