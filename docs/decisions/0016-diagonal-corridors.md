# 0016: Diagonal corridors: inner diagonal walls and the `partition_diagonal` layout

Status: accepted (2026-10-06)

## Context

ADR 0014 gave buildings diagonal walls only as corner cuts on the outside. Cyberpunk maps
look better with 45 degree corridors, which need diagonal walls *between rooms*, doors in
them, and a layout that plans for them. A first try that dropped one diagonal corridor into
random offices (a chance in the building type) looked patchy: leftovers beside the band turned
into corridor blobs and walls stopped and started.

## Decision

- An *inner diagonal* is a `Diagonal` whose cut triangle is floor, not open air
  (`Floor.is_inner_diagonal`). A door in one is an `Opening` with `diagonal: true`; its
  `edges` are the horizontal cut edge of each diagonal cell it spans.
- Diagonals are made by a separate layout, `partition_diagonal` (a subclass of `partition`,
  chosen with `GenerationParams.layout` / `--layout` / the UI's Layout select), not by a
  random chance in the building type. It has no straight cross corridors: parallel corridors
  are joined by 45 degree bands, one per pair, and more of them 50-70 cells apart on long
  corridors.
- A band is a parallelogram, one row per step, so it meets the corridors square. Once the
  rooms are typed, `_bevels` cuts the room cells at the steps with a diagonal, the corner
  triangle going to the corridor. Leftovers beside a band go to the flanking rooms, never to
  the corridor, so the walls stay continuous.
- Widths: a slanted wall cuts the horizontal and vertical runs of the cells next to it short.
  `thinnest_slanted` ignores a run that ends at a diagonal cell, and counts a cell pinned in
  both directions (the tip of a triangle) as thick. Straight arms still count.

## Consequences

- Plain `partition` is unchanged (apart from the shared leftover handling being a no-op
  without a band).
- Rare hard width violations remain on some seeds, as in the plain layout; the generator keeps
  the best of its attempts.
