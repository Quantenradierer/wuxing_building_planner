# 0008: Greedy, rule-based furnishing with walkability checks

Status: accepted (2026-09-26)

## Decision

- Furniture is data: an object catalog (size, cover, glyph) and per-room `furniture:` rules
  with a small set of placements (`wall`, `corner`, `center`, `scatter`, `near_exit`,
  `rows`, and later `at` for objects beside other objects).
- Placement is greedy per rule. Hard constraints: inside the room, no overlap, door
  clearance free, the room's free floor stays connected. The connectivity check uses a
  ring test around the new object and only falls back to a flood fill when needed.
- Objects carry a cover value now, so the tactical layer can derive cover later.
- The stage is a strategy (`furnishing: rules`); WFC remains a candidate for clutter and
  the condition layer (ADR 0003).
