# Roomplanner

Generator for Shadowrun/cyberpunk building battle maps. Output: versioned JSON, ASCII debug
view, themed images (Pillow) and VTT exports (Universal VTT, Foundry scenes).

- Requirements, status and known limitations: `docs/requirements.md`
- Architecture (coordinates, data model, pipeline, layouts, JSON contract): `docs/architecture.md`
- Decisions and their reasons: `docs/decisions/`
- Sprites (Midjourney object pictures): `docs/sprites.md`; read it before generating sprites
  or adding an object kind

Read the architecture doc before changing the model, the pipeline or the JSON format.

## Commands

```bash
uv sync                          # install
uv run pytest                    # tests (UPDATE_SNAPSHOTS=1 to rewrite snapshots, review diffs)
uv run ruff check . && uv run ruff format --check .
uv run pyright                   # strict mode
uv run roomplanner generate --type office --width 60 --depth 40 --floors-above 3 --seed 1
uv run roomplanner generate -t office -w 48 -d 30 -f png -o /tmp/office.png --labels
uv run roomplanner generate -t hotel -w 56 -d 36 --floors-above 2 -f foundry -o /tmp/hotel
```

uv is installed inside `.venv` (`.venv/bin/uv`) if it's not on PATH.

## Conventions

- All units are cells (1 cell = 0.5 m; meters only as the JSON scale hint); walls, doors and
  windows are on edges between cells (`Edge(x, y, axis)`).
- Building types are data (`src/roomplanner/data/`); code is generic. Prefer adding YAML
  over special cases in Python.
- Model classes are immutable; the JSON mapping is explicit in `serialization.py`. Breaking
  JSON changes bump `SCHEMA_VERSION`, optional fields don't.
- Every pipeline stage is a replaceable strategy; the validator is algorithm-agnostic and
  the property tests must hold for any strategy.
- All randomness flows from `Context.rng(stage)`; never use the global `random` module.
