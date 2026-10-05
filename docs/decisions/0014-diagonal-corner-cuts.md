# 0014: 45° corner cuts as diagonals across cells

Status: accepted (2026-10-03)

## Context

Every wall was a cell edge, so buildings had only horizontal and vertical walls. Chamfered
corners and angled facades are common in the target settings (corporate towers, plazas), and
GMs want them on maps. Free-angle walls or polygon rooms would replace the cell grid that
layout, doors, furniture and the validator are built on (ADR 0001).

## Decision

- A `Diagonal(x, y, cut)` is a 45° wall across one cell that cuts one corner off. Rooms, the
  footprint and objects stay sets of cells; a diagonal cell is half floor.
- The cut cell edges stay in `Floor.walls` and the validator's border invariant is
  unchanged. They are hidden (`Floor.cut_edges`): not drawn, exported, or used for openings.
- Diagonals come from the seed (no parameter: one building in three gets cuts of 4 to 10 cells): the pipeline cuts the
  convex footprint corners after the layout, so layouts are unchanged. Corners that are too
  tight, or whose cut would damage a room, stay square.
- Furnishing treats half cells as covered floor. The serialization adds an optional
  `diagonals` field per floor (no schema bump); the renderer, ASCII view and both VTT
  exports draw the diagonal.

## Consequences

- No changes to layout strategies, door placement or the JSON edge model.
- Diagonals appear only at building corners, not inside buildings, and only at 45°. Wedge
  rooms, angled interior walls and windows or doors in diagonals would need openings that
  are not an axis-aligned edge run; they are left for later.
- Objects keep a gap of up to a cell between them and a diagonal wall.
