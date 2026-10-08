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
  (34 shapes: 24 university, 6 office, 3 school, 1 commercial; only ones with real bends and no skeleton jitter). Corridors come only from buildings tagged office, university, school, hospital, commercial or government; stations, tunnels and anything below ground are left out. Public OSM has few such mapped corridors (hospitals and government buildings gave none in the cities searched), so the set is small; widen `REGIONS` to grow it.
  Data (c) OpenStreetMap contributors, ODbL; the YAML header says so.
- `layout: partition_osm` (`pipeline/layout/osm.py`, opt-in, `--layout partition_osm`)
  subclasses the partition layout and replaces `_bands`/`_diagonal`: it picks a shape
  (rng stream `osm`), one of eight rotations/mirrors that fits the footprint, scales it
  uniformly, thickens axis runs to the corridor width and 45° runs to staircase bands. Those
  bands feed the existing bevel step, so the rooms beside them get diagonal walls and doors.
  Branching, lobby, core, regions and typing are unchanged and fill what the shape leaves.
- `shape: osm` (`pipeline/footprint_osm.py`, `tools/osm_footprints.py`, `data/osm_footprints.yaml`)
  gives the footprint the outline of a real building: 160 outlines (house, apartments, office,
  commercial; only L, T, U and bay shapes whose walls are mostly axis-parallel), resampled to
  the requested width and depth.
- Credit: the CLI prints the OpenStreetMap notice for either, the web form shows it when
  either is selected, `docs/third-party.md` states the ODbL terms.
- Arbitrary angles are snapped to 45°; curves become polylines.

## Consequences

- Refresh the data with `fetch` then `build`; the result is committed, the layout needs no
  network.
- The skeleton is uniformly scaled, so a shape that fits one axis leaves the other to the
  branching step.
