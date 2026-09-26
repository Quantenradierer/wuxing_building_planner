# Roomplanner

Generator for Shadowrun/cyberpunk building battle maps.

- Requirements: `docs/requirements.md`
- Architecture (coordinates, data model, pipeline, JSON contract): `docs/architecture.md`
- Decisions and their reasons: `docs/decisions/`

Read the architecture doc before changing the model or the JSON format.

## Commands

```bash
uv sync                          # install
uv run pytest                    # tests (UPDATE_SNAPSHOTS=1 to rewrite snapshots)
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run roomplanner generate --type office --width 40 --depth 24 --seed 1
```

uv is installed inside `.venv` (`.venv/bin/uv`) if it's not on PATH.

## Conventions

- All units are cells (1 cell = 0.5 m, meters only as JSON scale hint); walls/doors/windows
  are on edges between cells (`Edge(x, y, axis)`).
- Model classes are immutable dataclasses; JSON mapping is explicit in `serialization.py`.
  Breaking JSON changes bump `SCHEMA_VERSION`.
- Every pipeline stage is a replaceable strategy; the validator is algorithm-agnostic.
- All randomness flows from the run seed; never use the global `random` module.
