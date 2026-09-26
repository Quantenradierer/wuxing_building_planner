# 0011: VTT export — Universal VTT first, Foundry scenes with an import macro

Status: accepted (2026-09-26)

## Context

The final goal is play in virtual tabletops. Universal VTT (`.dd2vtt`) is read by many
VTTs (Foundry via a module, Fantasy Grounds, Arkenforge, EncounterPlus, …); Foundry's own
format can express more (locked doors, see-through windows, teleporting stairs).

## Decision

- Both exporters read the model only and reuse the image renderer. The image is padded by
  one grid square; the grid size is a per-export parameter (default 1 m = 2 × 2 cells).
- **Universal VTT**: one file per floor, image embedded. Walls without openings and
  barricaded doors are walls; intact and broken doors are portals (broken ones open);
  windows are left out (sight and light pass; the format has no window type). Lights are
  exported unless switched off.
- **Foundry VTT (v12+)**: one scene per floor with walls (windows block movement only,
  locked doors are locked), lights (flicker → torch animation) and, for every stairwell and
  elevator, "up" / "down" Scene Regions with the native Teleport Token behaviour pointing at
  an arrival region on the neighbouring floor. Cross-scene references need stable ids, so
  the export is a folder (images, `scenes.json`, `import-macro.js`) whose macro creates the
  scenes with `keepId: true`; ids are derived from the seed and the export name.
- Lighting is left to the VTT by default: exported images carry no light map, Foundry
  scenes start at darkness 0.6 and the exported lights do the work. `--baked-lighting`
  bakes the light map into the image instead (VTT stays bright). Decided with the user.
- No module dependencies; Levels support, adventure compendia and Roll20 stay later work.
