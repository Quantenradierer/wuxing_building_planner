"""House-shaped footprints from OpenStreetMap building outlines.

    python tools/osm_footprints.py fetch raw/      # Overpass: building outlines, a few cities
    python tools/osm_footprints.py build raw/      # writes src/roomplanner/data/osm_footprints.yaml

Each outline is turned so its longest wall is axis-parallel, kept only if most of its walls are
(L, T, U, bay and porch shapes, not slanted or round ones), and rasterised in cells of 0.5 m.
The `osm` footprint shape resamples one of them to the requested width and depth.

Data (c) OpenStreetMap contributors, ODbL: https://www.openstreetmap.org/copyright
"""

from __future__ import annotations

import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import yaml
from PIL import Image, ImageDraw

OVERPASS = [  # public instances, tried in turn
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
REGIONS = [  # name, lat, lon, radius (m): residential areas
    ("berlin-zehlendorf", 52.435, 13.26, 2500),
    ("hamburg-blankenese", 53.56, 9.81, 2500),
    ("munich-solln", 48.08, 11.54, 2500),
    ("cologne-lindenthal", 50.93, 6.90, 2500),
    ("frankfurt-bornheim", 50.13, 8.71, 2500),
    ("paris-banlieue", 48.82, 2.45, 2500),
    ("london-suburb", 51.45, -0.05, 2500),
    ("boston-dorchester", 42.30, -71.07, 2500),
    ("tokyo-setagaya", 35.64, 139.65, 2500),
    ("amsterdam", 52.35, 4.95, 2500),
    ("berlin-mitte", 52.52, 13.40, 3000),  # dense city: blocks, hotels, schools, hospitals
    ("frankfurt-city", 50.11, 8.68, 3000),
    ("munich-city", 48.14, 11.58, 3000),
    ("hamburg-city", 53.55, 9.99, 3000),
    ("vienna", 48.21, 16.37, 3000),
    ("zurich", 47.37, 8.54, 3000),
    ("london-city", 51.51, -0.12, 3000),
    ("paris-city", 48.86, 2.35, 3000),
    ("chicago", 41.88, -87.63, 3000),
    ("new-york", 40.75, -73.99, 3000),
    ("tokyo-shinjuku", 35.69, 139.70, 3000),
    ("ruhr-essen", 51.46, 7.01, 3000),  # industry and warehouses
    ("rotterdam-port", 51.92, 4.48, 3000),
]
CATEGORIES = {
    "house": '["building"~"^(house|detached|semidetached_house|villa|bungalow)$"]',
    "apartments": '["building"="apartments"]',
    "office": '["building"="office"]',
    "commercial": '["building"~"^(commercial|retail)$"]',
    "hotel": '["building"="hotel"]',
    "school": '["building"~"^(school|kindergarten)$"]',
    "university": '["building"~"^(university|college)$"]',
    "hospital": '["building"="hospital"]',
    "industrial": '["building"="industrial"]',
    "warehouse": '["building"="warehouse"]',
    "church": '["building"~"^(church|cathedral|chapel)$"]',
    "government": '["building"~"^(government|civic|public)$"]',
    "parking": '["building"="parking"]',
}
PER_CATEGORY = 40
CELL = 0.5  # m
MIN_SIDE, MAX_SIDE = 7.0, 70.0  # m, bounding box of the outline
MIN_AREA = 50.0  # m2
AXIS_SHARE = 0.85  # of the wall length must lie within AXIS_ANGLE of an axis
AXIS_ANGLE = 8.0  # degrees
OUT = Path(__file__).resolve().parent.parent / "src/roomplanner/data/osm_footprints.yaml"

Point = tuple[float, float]


def fetch(folder: Path, only: list[str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for category, selector in CATEGORIES.items():
        if only and category not in only:
            continue
        for name, lat, lon, radius in REGIONS:
            target = folder / f"{category}_{name}.json"
            if target.exists():
                continue
            query = (
                f"[out:json][timeout:120];way{selector}(around:{radius},{lat},{lon});out geom 500;"
            )
            for attempt in range(6):
                request = urllib.request.Request(
                    OVERPASS[attempt % len(OVERPASS)],
                    data=urllib.parse.urlencode({"data": query}).encode(),
                    headers={"User-Agent": "roomplanner/0.1 (building footprints)"},
                )
                try:
                    with urllib.request.urlopen(request, timeout=200) as response:
                        target.write_bytes(response.read())
                    break
                except OSError as error:
                    print(f"{category} {name}: {error}", file=sys.stderr)
                    time.sleep(10 * (attempt + 1))
            time.sleep(3)


def project(geometry: list[dict[str, float]]) -> list[Point]:
    lat0, lon0 = geometry[0]["lat"], geometry[0]["lon"]
    kx = 111_320 * math.cos(math.radians(lat0))
    return [((p["lon"] - lon0) * kx, -(p["lat"] - lat0) * 110_540) for p in geometry[:-1]]


def simplify(points: list[Point], tolerance: float) -> list[Point]:
    """Drop vertices that lie within `tolerance` of the line between their neighbours."""
    changed = True
    while changed and len(points) > 4:
        changed = False
        for i in range(len(points)):
            a, b, c = points[i - 1], points[i], points[(i + 1) % len(points)]
            length = math.dist(a, c)
            if length == 0:
                points.pop(i)
                changed = True
                break
            if abs((c[0] - a[0]) * (a[1] - b[1]) - (a[0] - b[0]) * (c[1] - a[1])) / length < (
                tolerance
            ):
                points.pop(i)
                changed = True
                break
    return points


def straighten(points: list[Point]) -> list[Point] | None:
    """Turn the outline so its longest wall is axis-parallel; None if too many walls slant."""
    edges = list(zip(points, [*points[1:], points[0]], strict=True))
    longest = max(edges, key=lambda e: math.dist(*e))
    angle = math.atan2(longest[1][1] - longest[0][1], longest[1][0] - longest[0][0])
    c, s = math.cos(-angle), math.sin(-angle)
    turned = [(x * c - y * s, x * s + y * c) for x, y in points]
    total = straight = 0.0
    for a, b in zip(turned, [*turned[1:], turned[0]], strict=True):
        length = math.dist(a, b)
        direction = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 90
        total += length
        if min(direction, 90 - direction) <= AXIS_ANGLE:
            straight += length
    return turned if total and straight / total >= AXIS_SHARE else None


def rasterise(points: list[Point]) -> list[str] | None:
    x0, y0 = min(p[0] for p in points), min(p[1] for p in points)
    wide = max(p[0] for p in points) - x0
    high = max(p[1] for p in points) - y0
    if min(wide, high) < MIN_SIDE or max(wide, high) > MAX_SIDE:
        return None
    size = (math.ceil(wide / CELL) + 1, math.ceil(high / CELL) + 1)
    image = Image.new("1", size, 0)
    ImageDraw.Draw(image).polygon([((x - x0) / CELL, (y - y0) / CELL) for x, y in points], fill=1)
    rows = [
        "".join("#" if image.getpixel((x, y)) else "." for x in range(size[0]))
        for y in range(size[1])
    ]
    area = sum(r.count("#") for r in rows) * CELL * CELL
    return rows if area >= MIN_AREA else None


def corners(rows: list[str]) -> int:
    """Convex plus concave corners of the cell shape: 4 for a rectangle, 6 for an L."""
    h, w = len(rows), len(rows[0])

    def on(x: int, y: int) -> bool:
        return 0 <= x < w and 0 <= y < h and rows[y][x] == "#"

    count = 0
    for y in range(h + 1):
        for x in range(w + 1):
            around = [on(x - 1, y - 1), on(x, y - 1), on(x - 1, y), on(x, y)]
            if sum(around) in (1, 3):
                count += 1
    return count


def build(folder: Path) -> None:
    found: list[dict] = []
    seen: set[int] = set()
    for file in sorted(folder.glob("*.json")):
        category = file.name.split("_")[0]
        for way in json.loads(file.read_text())["elements"]:
            if way.get("type") != "way" or "geometry" not in way or way["id"] in seen:
                continue
            geometry = way["geometry"]
            if len(geometry) < 6 or geometry[0] != geometry[-1]:
                continue
            seen.add(way["id"])
            points = simplify(project(geometry), 0.4)
            if len(points) < 6:
                continue
            turned = straighten(points)
            rows = rasterise(turned) if turned else None
            if rows is None:
                continue
            n = corners(rows)
            if not 6 <= n <= 20:
                continue
            found.append(
                {
                    "id": f"osm-{way['id']}",
                    "category": category,
                    "corners": n,
                    "rows": rows,
                }
            )
    earlier = yaml.safe_load(OUT.read_text()) if OUT.exists() else []
    known = {e["id"] for e in earlier}
    found = [f for f in found if f["id"] not in known]
    kept: list[dict] = []
    for category in CATEGORIES:
        old = [e for e in earlier if e["category"] == category]
        mine = sorted((f for f in found if f["category"] == category), key=lambda f: -f["corners"])
        # earlier outlines stay (reproducible seeds); new ones fill up to the cap
        mine = mine[:: max(1, len(mine) // max(1, PER_CATEGORY - len(old)))]
        mine = mine[: max(0, PER_CATEGORY - len(old))]
        kept += old
        # spread over the corner counts: every other one from the interesting end
        chosen = mine
        kept += chosen
        print(f"{category}: {len(old)} kept, {len(chosen)} new")
    kept.sort(key=lambda f: f["id"])
    header = (
        "# Building outlines, one cell = 0.5 m, # = inside.\n"
        "# Generated by tools/osm_footprints.py from OpenStreetMap buildings (way id in `id`).\n"
        "# (c) OpenStreetMap contributors, Open Database License 1.0:\n"
        "# https://www.openstreetmap.org/copyright. This file is a derived database and stays\n"
        "# under the ODbL; see docs/third-party.md.\n"
    )
    OUT.write_text(header + yaml.safe_dump(kept, width=200, default_flow_style=None))
    print(f"{len(kept)} outlines -> {OUT}")


if __name__ == "__main__":
    command, target = sys.argv[1], Path(sys.argv[2])
    if command == "fetch":
        fetch(target, sys.argv[3:])  # optional: only these categories
    else:
        build(target)
