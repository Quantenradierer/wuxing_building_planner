# 0004: Failure policy: fail fast when impossible, otherwise retry then best effort

Status: accepted (2026-09-26)

## Decision

1. A feasibility pre-check computes whether the required program (core, entrances,
   corridors, `required` rooms at minimum size) fits each floor. If not, generation fails
   immediately with an error naming the floor, the needed and the available area.
   Impossible inputs are never retried.
2. Hard validator violations trigger a retry with a seed derived from the main seed.
3. After N retries, the best attempt is returned. Dropped rooms and soft violations are
   warnings, stored in the JSON and printed by the CLI.
4. Determinism: same params and seed produce the same output, retries included.
