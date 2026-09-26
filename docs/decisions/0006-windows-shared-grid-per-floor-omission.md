# 0006: Windows on a shared facade grid, omitted per floor

Status: accepted (2026-09-26), refines the "identical windows" requirement

## Context

Windows were first identical on every floor: a window colliding with a partition, door or
windowless room on *any* floor was dropped everywhere. Buildings with many windowless rooms
(clinics: x-ray, pharmacy, operating rooms) and entrances ended up with long blank facades
and many window-requiring rooms without windows.

## Decision

- All floors above ground share one window grid per facade (aligned with the layout's
  partition grid), so windows line up vertically.
- Each floor omits only the windows that collide with its own walls, doors or windowless
  rooms.

## Consequences

Facades look regular, rooms that need windows get them; floors may differ where a floor has
a windowless room or an entrance.
