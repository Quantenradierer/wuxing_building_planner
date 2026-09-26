# Roomplanner

Parameterized generator for Shadowrun/cyberpunk building battle maps: offices, clinics,
apartment blocks and supermarkets, from a corner stuffer shack to a corp tower floor.

All sizes are in cells (1 cell = 0.5 m, so a 1.5 m battle square is 3×3 cells). Walls, doors
and windows sit on the edges between cells.

```bash
uv sync
uv run roomplanner generate --type office --width 60 --depth 40 --floors-above 3 --seed 1
uv run roomplanner generate -t apartment -w 64 -d 36 --floors-above 4 --floors-below 1 --wealth low
uv run roomplanner generate -t clinic -w 72 -d 48 --shape l --street-side E --service-side N
uv run roomplanner generate -t supermarket -w 80 -d 50 --wealth luxury -f json -o megamart.json
uv run roomplanner render megamart.json
```

## Parameters

| Option                 | Values                                                 |
|------------------------|--------------------------------------------------------|
| `-t/--type`            | office, clinic, apartment, supermarket                 |
| `-w/--width`, `-d/--depth` | building bounding box in cells                     |
| `--floors-above`       | floors incl. ground floor (default 1)                  |
| `--floors-below`       | basements (default 0)                                  |
| `--wealth`             | squatter, low, middle, high, luxury                    |
| `--shape`              | rectangle, l                                           |
| `--street-side`        | N, E, S, W: main entrance (default S)                  |
| `--service-side`       | N, E, S, W: loading dock / back door (default opposite)|
| `--condition`, `--security` | stored for later layers                           |
| `--seed`               | same seed, same building                               |
| `-f/--format`          | ascii (debug view) or json (full model)                |

Impossible inputs fail with an explanation (e.g. "too small for an L shape, both arms need
at least 14 cells"); tight ones return a building plus warnings about dropped rooms.

## Output

The JSON (`schema_version` 2) contains floors with rooms (cells, type, apartment unit),
walls and openings (doors with swing, windows) as cell edges, and furniture as rectangles
with facing and cover-relevant kind. It is the contract for the image renderer to come.

In the ASCII view `- |` are walls, `D` doors, `=`/`"` windows, letters furniture and
numbers rooms (legend below each floor).

## Library

```python
from roomplanner import GenerationParams, generate
from roomplanner.serialization import to_json

building = generate(GenerationParams(building_type="office", width=60, depth=40, seed=7))
print(to_json(building))
```

## Extending

Building types are data: `src/roomplanner/data/buildings/*.yaml` (programs) and
`data/rooms/*.yaml` (room catalogs), `data/objects.yaml` (furniture), `data/wealth.yaml`.
Algorithms are replaceable strategies selected from the YAML, by name or import path.

Read `docs/requirements.md`, `docs/architecture.md` and `docs/decisions/` before changing
the model or the JSON format.

## Development

```bash
uv run pytest            # UPDATE_SNAPSHOTS=1 rewrites the ASCII snapshots
uv run ruff check . && uv run ruff format --check .
uv run pyright
```
