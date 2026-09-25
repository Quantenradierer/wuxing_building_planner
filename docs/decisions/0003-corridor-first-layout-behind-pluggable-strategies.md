# 0003: Corridor-first layout behind pluggable strategies

Status: accepted (2026-09-26)

## Context

Candidates for room placement: recursive subdivision (BSP), corridor-first + treemap,
growth from seeds, constraint solving, Wave Function Collapse (WFC).

WFC was considered and rejected **for room placement**. It enforces only local tile
adjacency. A floor plan is dominated by global constraints: room counts and areas, program
shares, reachability, windows. WFC offers no control over room size, no connectivity
guarantee, contradictions on large grids, and texture-like results rather than realistic
buildings.

## Decision

- Default room layout: corridor-first. Core, then corridor network from the entrance past
  the core, then strips split into rooms as a squarified treemap. Variants `corridor`,
  `units`, `hall`.
- **Every pipeline stage is a replaceable strategy** behind a `Protocol`, registered by
  name and selected from YAML. Stages only communicate through the data model.
- The validator is independent of the algorithm, so any replacement is checked against
  the same invariants (also by the property-based tests).

## Consequences

- Algorithms can be swapped or compared without touching other stages.
- WFC remains a good candidate for the furnishing and condition layers, and could be tried
  as an experimental layout strategy.
