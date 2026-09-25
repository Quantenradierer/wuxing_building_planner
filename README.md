# Roomplanner

Parameterized generator for Shadowrun/cyberpunk building battle maps.

```bash
uv sync
uv run roomplanner generate --type office --width 20 --depth 12 --floors-above 2 --seed 1
uv run roomplanner generate --type office --width 20 --depth 12 --format json -o map.json
uv run roomplanner render map.json
```

See `docs/` for requirements, architecture and design decisions.
