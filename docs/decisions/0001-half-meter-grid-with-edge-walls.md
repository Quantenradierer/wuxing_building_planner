# 0001: Half-meter grid with walls on edges

Status: accepted (2026-09-26)

## Context

Battle maps are usually drawn on 1.5 m (5 ft) squares. Buildings, however, need finer
resolution: a door is ~1 m, a toilet stall ~1 m wide, a corridor 1.5–2 m.
If walls occupy whole cells, every wall eats a cell of floor space, and at coarse
resolutions that distorts rooms badly.

## Decision

- 1 cell = 0.5 m. A 1.5 m battle square is 3×3 cells and can be overlaid by a renderer.
- Walls, doors and windows are on the **edges between cells**, not in cells.

## Consequences

- Architecturally correct thin walls; the future image renderer draws them directly.
- Maps have many cells (a 1000 m² floor is ~4000 cells). Algorithms must not be
  per-cell expensive.
- ASCII rendering needs a doubled grid, which is fine because ASCII is a debug view only.

## Addendum (2026-09-26): cells everywhere

Parameters, rule files and output use cells, not meters. Converting meters caused rounding
surprises and made the rules harder to reason about on the grid. `cell_size_m` remains in the
JSON as the physical scale for renderers.
