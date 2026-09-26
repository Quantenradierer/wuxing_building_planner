# 0012: Object sprites generated with Midjourney

Status: accepted (2026-09-26)

## Context

The procedural object shapes (ADR 0009) are readable but plain. Painted top-down furniture
makes the maps look like hand-made battle maps. There is no licensed asset pack, but the
author has Midjourney access (the Discord accessor used by the SINner project).

## Decision

- Sprites are PNGs with alpha, one per object kind, in `data/sprites/<set>/<kind>.png`. A
  theme names its set with `sprites:`; objects without a sprite keep their shape. The
  bundled `neon_sprites` theme `extends: neon` and uses the `midjourney` set.
- Wealth variants are `<kind>.<wealth>.png` (e.g. `bed.high.png`). The object's tier
  (`PlacedObject.wealth`, else the building's wealth) picks its variant, else the variant of
  the next tier towards middle, else the plain `<kind>.png`: luxury uses `.high`, squatter
  uses `.low`, but low and high never borrow the extremes (the default sprites already look
  worn, a squatter mattress would be wrong in a low-tier flat). Variants have their own
  prompt style in `variants:` of the prompt file; the base style reference at a lower
  weight keeps the painted look while the materials change. Flat files keep the set a
  single folder.
- Convention: a sprite faces S, i.e. the object's back (the wall side) is at the top and
  the picture spans `[along, deep]` of `objects.yaml`. The renderer rotates it to the
  object's `facing` and stretches it to the object's box.
- Generation is a dev tool, not part of the package: `tools/sprites.py` sends one /imagine
  per kind (prompts in `tools/sprite_prompts.yaml`, aspect ratio from the object size, a
  seed derived from kind and attempt), downloads the 2x2 grid to `.sprites_raw/`, keys out
  the white background by flood fill from the border, trims, and scales to 96 px per cell.
  One shared prompt style plus a style reference (`--sref`, one of our own grids) keeps
  the set in a single look. The chosen quadrant (and a correcting rotation) per kind is a
  manual pick recorded in `tools/sprite_picks.yaml`.
- Stairs, elevator cars, wall screens and whiteboards stay procedural: their exact geometry
  matters more than texture, or they are too thin to read from above.

## Consequences

- Sprites are generated artwork under the author's Midjourney terms; regenerate or swap
  the set if the maps are to be distributed differently.
- The keying struggles with pale objects and soft shadows; bad picks are regenerated with
  another `--attempt`.
- Sprites don't glow; neon accents on objects exist only in the procedural theme.
