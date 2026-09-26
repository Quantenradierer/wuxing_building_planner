# 0010: Lights, security and condition as data-driven layers after furnishing

Status: accepted (2026-09-26)

## Context

Security and condition were stored-only parameters in v1. Both, and lights, change a
finished floor rather than its layout, except that security may add rooms.

## Decision

- Each is a pipeline stage after furnishing (lights → security → condition), a replaceable
  strategy like the others, with its rules in one YAML file shared by all building types.
- Security rooms are ordinary program entries filtered by `security:` (like `wealth:`).
- The model grows optional data only: door state / material / lock / rating / entrance,
  `Device` and `Light` collections; plus the `breach` opening kind, which bumped the JSON
  schema to 3 (version 2 is still read).
- Condition may never break reachability: doors are blocked only after a reachability
  check and never on the facade; breaches only add connections; debris and furniture
  removal keep each room's free floor connected. The validator checks the same invariants.
- Visual wear (stains, graffiti) is not in the model: the renderer derives it from the
  building's condition via the theme.
- The tactical layer (cover, terrain) stays postponed.
