# 0009: Procedural image renderer with swappable themes

Status: accepted (2026-09-26)

## Context

Battle maps must be usable in VTTs, which need one raster image per map. Art assets would
look better but bring licences and a dependency on packs we don't have yet.

## Decision

- The renderer reads only the model (i.e. the JSON), never the building rules.
- Pillow draws everything procedurally: floors with materials and grain, soft shadows,
  objects as parameterised shapes, walls, windows, doors with swing arcs, and a blurred
  glow layer for neon accents. The picture is drawn at 2× and scaled down for
  anti-aliasing (1× for very large maps, to bound memory).
- Everything visual is a **theme** (YAML): room type → material, object kind → shape and
  colours, wall / door / window styles. Unknown types fall back to defaults. A textured
  asset pack becomes another theme later (e.g. an `image:` field per material or object)
  without touching the pipeline.
- The image is padded by whole VTT grid squares (default 2 cells = 1 m) so the grid stays
  aligned with the cells; the default resolution is 50 px per cell.

## Consequences

- No native dependencies (Pillow wheels exist for Python 3.14); rendering a large floor
  takes a few seconds.
- The look is functional rather than painterly until an asset theme exists.
