# 0017: Corridors from OpenStreetMap indoor mapping

Status: accepted (2026-10-08)

## Context

The partition layout cut straight, axis-parallel corridors (plus the occasional 45° link,
ADR 0016), so every building looked alike. Real buildings have bent, branching and angled
corridors.

## Decision

- `tools/osm_corridors.py` fetches `indoor=corridor` areas from Overpass (several large
  cities), joins touching areas of one level into a network, rasterises it, thins it to a
  centreline, prunes spurs, simplifies it and snaps it to the eight directions the model can
  draw (axis-parallel and 45°) in cells of 0.5 m. Result: `data/osm_corridors.yaml`
  (~80 shapes, only ones with real bends and no skeleton jitter, about half with diagonals).
  Data (c) OpenStreetMap contributors, ODbL; the YAML header says so.
- `layout: partition_osm` (`pipeline/layout/osm.py`, opt-in, `--layout partition_osm`)
  subclasses the partition layout and replaces `_bands`/`_diagonal`: it picks a shape
  (rng stream `osm`), one of eight rotations/mirrors that fits the footprint, scales it
  uniformly, thickens axis runs to the corridor width and 45° runs to staircase bands. Those
  bands feed the existing bevel step, so the rooms beside them get diagonal walls and doors.
  Branching, lobby, core, regions and typing are unchanged and fill what the shape leaves.
- Arbitrary angles are snapped to 45°; curves become polylines.

## Consequences

- Refresh the data with `fetch` then `build`; the result is committed, the layout needs no
  network.
- The skeleton is uniformly scaled, so a shape that fits one axis leaves the other to the
  branching step.
