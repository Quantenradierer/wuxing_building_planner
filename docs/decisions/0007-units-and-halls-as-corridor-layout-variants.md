# 0007: Units and halls as variants of the corridor layout

Status: accepted (2026-09-26)

## Context

ADR 0003 planned three layout strategies: `corridor`, `units` (apartments) and `hall`
(supermarket). Implementing them separately would duplicate footprint splitting, bands,
core, entrances, connectors for L shapes and room allocation.

## Decision

- **Units** are a room kind, not a layout: a program lists unit types under `units:`; the
  allocator places a unit like any full-depth room and `layout/units.py` subdivides it.
  Offices could have units (rented suites) just as apartment buildings do.
- **Hall** is a subclass of the corridor layout that only changes the main part's bands
  (`[hall | service corridor | back of house]`); everything else is inherited.

## Consequences

One layout engine to maintain; new building types mostly pick bands, units and data. A
genuinely different algorithm (e.g. WFC) still plugs in as its own strategy.
