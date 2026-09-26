# Roomplanner

Parameterized generator for Shadowrun/cyberpunk building battle maps.
All sizes are in cells (1 cell = 0.5 m).

```bash
uv sync
uv run roomplanner generate --type office --width 40 --depth 24 --floors-above 2 --seed 1
uv run roomplanner generate --type office --width 40 --depth 24 --format json -o map.json
uv run roomplanner render map.json
```

See `docs/` for requirements, architecture and design decisions.
