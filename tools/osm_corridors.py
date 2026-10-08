"""Corridor shapes from OpenStreetMap indoor mapping (`indoor=corridor` areas).

    python tools/osm_corridors.py fetch raw.json      # Overpass query, a few big cities
    python tools/osm_corridors.py build raw.json      # writes data/osm_corridors.yaml

Per building level the corridor areas that touch form one network. Each network is rasterised,
thinned to its centreline, simplified and snapped to the eight directions the layout can draw
(axis-parallel and 45 degrees), in cells of 0.5 m. The partition layout `partition_osm` scales
such a skeleton into the footprint and thickens it to the program's corridor width.

Data (c) OpenStreetMap contributors, ODbL: https://www.openstreetmap.org/copyright
Pure Python (Pillow for the polygon fill), so it runs in the project environment.
"""

from __future__ import annotations

import itertools
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

import yaml
from PIL import Image, ImageDraw

OVERPASS = [  # public instances, tried in turn
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
REGIONS = [  # name, lat, lon, radius (m)
    ("rhein-main", 50.11, 8.68, 12000),
    ("berlin", 52.52, 13.40, 12000),
    ("munich", 48.14, 11.58, 12000),
    ("hamburg", 53.55, 9.99, 12000),
    ("cologne", 50.94, 6.96, 12000),
    ("stuttgart", 48.78, 9.18, 12000),
    ("karlsruhe", 49.01, 8.40, 12000),
    ("dresden", 51.05, 13.74, 12000),
    ("vienna", 48.21, 16.37, 12000),
    ("zurich", 47.37, 8.54, 12000),
    ("amsterdam", 52.37, 4.90, 12000),
    ("paris", 48.86, 2.35, 12000),
    ("london", 51.51, -0.12, 12000),
]
# category: Overpass selectors of the buildings the corridors must lie in
CATEGORIES = {
    "office": ['way["building"="office"]'],
    "university": ['way["building"~"^(university|college)$"]'],
    "school": ['way["building"="school"]'],
    "hospital": ['way["building"="hospital"]', 'way["amenity"="hospital"]["building"]'],
    "commercial": ['way["building"~"^(commercial|retail)$"]'],
    "government": ['way["building"~"^(government|civic|public)$"]'],
}
PER_CATEGORY = 24  # shapes kept per category
EXCLUDED = ("tunnel", "railway", "public_transport", "subway", "bridge", "highway", "area:highway")
CELL = 0.5  # m
PIXEL = 0.25  # m, raster resolution
MIN_AREA = 40.0  # m2 of corridor in a network
MIN_EXTENT = 12.0  # m, longest side of a network
MAX_EXTENT = 110.0
SPUR = 2.5  # m: leaf branches shorter than this are skeleton noise
TOLERANCE = 0.7  # m, line simplification
MAX_TEMPLATES = 120
MIN_SEGMENT = 4  # cells: shorter pieces are jitter of the skeleton
MAX_BENDS = 16
OUT = Path(__file__).resolve().parent.parent / "src/roomplanner/data/osm_corridors.yaml"

Point = tuple[float, float]


def fetch(folder: Path) -> None:
    """One Overpass query per category and region: the corridors inside such buildings."""
    folder.mkdir(parents=True, exist_ok=True)
    for category, selectors in CATEGORIES.items():
        for name, lat, lon, radius in REGIONS:
            target = folder / f"{category}_{name}.json"
            if target.exists():
                continue
            buildings = "".join(f"{sel}(around:{radius},{lat},{lon});" for sel in selectors)
            query = (
                f"[out:json][timeout:240];({buildings})->.b;.b map_to_area->.a;"
                'way["indoor"="corridor"](area.a);out geom tags;'
            )
            for attempt in range(6):
                request = urllib.request.Request(
                    OVERPASS[attempt % len(OVERPASS)],
                    data=urllib.parse.urlencode({"data": query}).encode(),
                    headers={"User-Agent": "roomplanner/0.1 (corridor shapes)"},
                )
                try:
                    with urllib.request.urlopen(request, timeout=300) as response:
                        target.write_bytes(response.read())
                    break
                except OSError as error:
                    print(f"{category} {name}: {error}", file=sys.stderr)
                    time.sleep(10 * (attempt + 1))
            time.sleep(5)


# --- geometry ---------------------------------------------------------------------------


def project(geometry: list[dict[str, float]], lat0: float, lon0: float) -> list[Point]:
    kx = 111_320 * math.cos(math.radians(lat0))
    return [((p["lon"] - lon0) * kx, -(p["lat"] - lat0) * 110_540) for p in geometry]


def clusters(ways: list[dict]) -> list[list[dict]]:
    """Closed corridor areas of one level whose boxes touch (within 0.6 m) form a network."""
    areas = [w for w in ways if len(w["geometry"]) >= 4 and w["geometry"][0] == w["geometry"][-1]]
    for way in areas:
        lats = [p["lat"] for p in way["geometry"]]
        lons = [p["lon"] for p in way["geometry"]]
        way["_box"] = (min(lons), min(lats), max(lons), max(lats))
    grid = 0.0005
    cells: dict[tuple[int, int, str], list[int]] = {}
    for i, way in enumerate(areas):
        level = way["tags"].get("level", way["tags"].get("repeat_on", ""))
        x0, y0, x1, y1 = way["_box"]
        for gx in range(int(x0 / grid) - 1, int(x1 / grid) + 2):
            for gy in range(int(y0 / grid) - 1, int(y1 / grid) + 2):
                cells.setdefault((gx, gy, level), []).append(i)
    parent = list(range(len(areas)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    pad = 0.6 / 111_000
    for members in cells.values():
        for a in members:
            for b in members:
                if a < b:
                    ax0, ay0, ax1, ay1 = areas[a]["_box"]
                    bx0, by0, bx1, by1 = areas[b]["_box"]
                    if (
                        ax0 - pad <= bx1
                        and bx0 - pad <= ax1
                        and ay0 - pad <= by1
                        and by0 - pad <= ay1
                    ):
                        parent[find(a)] = find(b)
    groups: dict[int, list[dict]] = {}
    for i, way in enumerate(areas):
        groups.setdefault(find(i), []).append(way)
    return list(groups.values())


def raster(polygons: list[list[Point]]) -> tuple[set[tuple[int, int]], float] | None:
    xs = [p[0] for poly in polygons for p in poly]
    ys = [p[1] for poly in polygons for p in poly]
    x0, y0 = min(xs), min(ys)
    wide, high = max(xs) - x0, max(ys) - y0
    if max(wide, high) > MAX_EXTENT or max(wide, high) < MIN_EXTENT:
        return None
    size = (int(wide / PIXEL) + 5, int(high / PIXEL) + 5)
    image = Image.new("1", size, 0)
    draw = ImageDraw.Draw(image)
    for poly in polygons:
        draw.polygon([((x - x0) / PIXEL + 2, (y - y0) / PIXEL + 2) for x, y in poly], fill=1)
    pixels = {(x, y) for y in range(size[1]) for x in range(size[0]) if image.getpixel((x, y))}
    area = len(pixels) * PIXEL * PIXEL
    if area < MIN_AREA:
        return None
    return pixels, area


_RING = [(-1, -1), (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0)]


def thin(pixels: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """Zhang-Suen thinning on a pixel set."""
    pixels = set(pixels)
    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            doomed: list[tuple[int, int]] = []
            for x, y in pixels:
                p = [(x + dx, y + dy) in pixels for dx, dy in _RING]
                count = sum(p)
                if not 2 <= count <= 6:
                    continue
                if sum(not p[i] and p[(i + 1) % 8] for i in range(8)) != 1:
                    continue
                n, e, s, w = p[1], p[3], p[5], p[7]
                if step == 0 and ((n and e and s) or (e and s and w)):
                    continue
                if step == 1 and ((n and e and w) or (n and s and w)):
                    continue
                doomed.append((x, y))
            if doomed:
                pixels.difference_update(doomed)
                changed = True
    return pixels


def _neighbours(p: tuple[int, int], pixels: set[tuple[int, int]]) -> list[tuple[int, int]]:
    return [(p[0] + dx, p[1] + dy) for dx, dy in _RING if (p[0] + dx, p[1] + dy) in pixels]


class Graph:
    """Skeleton graph: nodes are ids with positions, edges are (a, b, polyline)."""

    def __init__(self) -> None:
        self.nodes: dict[int, Point] = {}
        self.edges: list[tuple[int, int, list[Point]]] = []

    @staticmethod
    def of(skeleton: set[tuple[int, int]]) -> Graph:
        graph = Graph()
        special = {p for p in skeleton if len(_neighbours(p, skeleton)) != 2}
        # adjacent special pixels are one junction
        node_of: dict[tuple[int, int], int] = {}
        for seed in sorted(special):
            if seed in node_of:
                continue
            ident = len(graph.nodes)
            stack, members = [seed], []
            node_of[seed] = ident
            while stack:
                cur = stack.pop()
                members.append(cur)
                for n in _neighbours(cur, special):
                    if n not in node_of:
                        node_of[n] = ident
                        stack.append(n)
            graph.nodes[ident] = (
                sum(m[0] for m in members) / len(members),
                sum(m[1] for m in members) / len(members),
            )
        seen: set[tuple[int, int]] = set()
        for start in sorted(special):
            for first in _neighbours(start, skeleton):
                if first in special or first in seen:
                    continue
                path = [first]
                seen.add(first)
                cur, prev = first, start
                end = None
                while end is None:
                    options = [n for n in _neighbours(cur, skeleton) if n != prev and n not in path]
                    nxt = next((n for n in options if n in special), None)
                    if nxt is None and options:
                        nxt = options[0]
                    if nxt is None:
                        break
                    if nxt in special:
                        end = nxt
                        break
                    seen.add(nxt)
                    path.append(nxt)
                    prev, cur = cur, nxt
                if end is None:
                    continue
                a, b = node_of[start], node_of[end]
                points = [
                    graph.nodes[a],
                    *[(float(p[0]), float(p[1])) for p in path],
                    graph.nodes[b],
                ]
                graph.edges.append((a, b, points))
        return graph

    def degree(self) -> Counter[int]:
        return Counter(n for a, b, _ in self.edges for n in (a, b))

    def length(self, edge: tuple[int, int, list[Point]]) -> float:
        return sum(math.dist(p, q) for p, q in zip(edge[2], edge[2][1:], strict=False)) * PIXEL

    def prune(self) -> None:
        """Drop short leaf branches, merge the nodes that end up with two edges."""
        while True:
            degree = self.degree()
            leaf = next(
                (
                    e
                    for e in self.edges
                    if (degree[e[0]] == 1) != (degree[e[1]] == 1)
                    and self.length(e) < SPUR
                    and degree[e[0]] != 0
                ),
                None,
            )
            if leaf is None:
                break
            self.edges.remove(leaf)
        self.merge()

    def merge(self) -> None:
        skip: set[int] = set()
        while True:
            degree = self.degree()
            node = next((n for n, d in degree.items() if d == 2 and n not in skip), None)
            if node is None:
                return
            touching = [e for e in self.edges if node in e[:2]]
            if len(touching) != 2:  # a loop on this node alone
                skip.add(node)
                continue
            (a1, b1, p1), (a2, b2, p2) = touching
            if a1 == node:
                a1, b1, p1 = b1, a1, p1[::-1]
            if b2 == node:
                a2, b2, p2 = b2, a2, p2[::-1]
            self.edges.remove(touching[0])
            self.edges.remove(touching[1])
            self.edges.append((a1, b2, [*p1, *p2[1:]]))


def simplify(points: list[Point], tolerance: float) -> list[Point]:
    if len(points) < 3:
        return points
    (x0, y0), (x1, y1) = points[0], points[-1]
    length = math.dist(points[0], points[-1])

    def offset(p: Point) -> float:
        if length == 0:
            return math.dist(p, points[0])
        return abs((x1 - x0) * (y0 - p[1]) - (x0 - p[0]) * (y1 - y0)) / length

    index, worst = max(((i, offset(p)) for i, p in enumerate(points[1:-1], 1)), key=lambda t: t[1])
    if worst <= tolerance:
        return [points[0], points[-1]]
    return [*simplify(points[: index + 1], tolerance)[:-1], *simplify(points[index:], tolerance)]


def octilinear(a: tuple[int, int], b: tuple[int, int]) -> list[tuple[int, int]]:
    """Cells from a to b by at most two steps: the longer piece first, one of the eight
    directions each."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    diag = min(abs(dx), abs(dy))
    sx, sy = (dx > 0) - (dx < 0), (dy > 0) - (dy < 0)
    straight = max(abs(dx), abs(dy)) - diag
    pieces: list[tuple[int, int, int]] = []
    if diag:
        pieces.append((sx, sy, diag))
    if straight:
        pieces.append((sx if abs(dx) > abs(dy) else 0, sy if abs(dy) > abs(dx) else 0, straight))
    if len(pieces) == 2 and pieces[1][2] > pieces[0][2]:
        pieces.reverse()
    result = [a]
    cur = a
    for ux, uy, n in pieces:
        cur = (cur[0] + ux * n, cur[1] + uy * n)
        result.append(cur)
    return result


def _align(values: list[float], gap: int) -> dict[int, int]:
    """Cell coordinates of the vertices, those within `gap` cells made equal."""
    ordered = sorted({round(v / CELL) for v in values})
    mapping: dict[int, int] = {}
    group: list[int] = []
    for v in ordered:
        if group and v - group[-1] > gap:
            mean = round(sum(group) / len(group))
            mapping.update({g: mean for g in group})
            group = []
        group.append(v)
    if group:
        mean = round(sum(group) / len(group))
        mapping.update({g: mean for g in group})
    return mapping


def skeleton_paths(graph: Graph) -> list[list[tuple[int, int]]]:
    """The graph's edges as octilinear polylines in cells (shared vertices aligned)."""
    simple = [
        (a, b, simplify([(x * PIXEL, y * PIXEL) for x, y in pts], TOLERANCE))
        for a, b, pts in graph.edges
    ]
    xs = [p[0] for _, _, pts in simple for p in pts]
    ys = [p[1] for _, _, pts in simple for p in pts]
    mx, my = _align(xs, 2), _align(ys, 2)
    paths: list[list[tuple[int, int]]] = []
    for _, _, pts in simple:
        cells = [(mx[round(x / CELL)], my[round(y / CELL)]) for x, y in pts]
        path = [cells[0]]
        for a, b in itertools.pairwise(cells):
            if a != b:
                path.extend(octilinear(a, b)[1:])
        # drop collinear middle vertices
        clean = [path[0]]
        for i in range(1, len(path) - 1):
            ux, uy = path[i][0] - clean[-1][0], path[i][1] - clean[-1][1]
            vx, vy = path[i + 1][0] - path[i][0], path[i + 1][1] - path[i][1]
            if ux * vy - uy * vx != 0:
                clean.append(path[i])
        clean.append(path[-1])
        if len(clean) >= 2 and clean[0] != clean[-1]:
            paths.append(clean)
    return paths


def interest(paths: list[list[tuple[int, int]]]) -> tuple[int, int]:
    """(bends and junctions, diagonal cells): what makes a shape worth keeping."""
    ends = Counter(p for path in paths for p in (path[0], path[-1]))
    bends = sum(len(path) - 2 for path in paths) + sum(1 for n in ends.values() if n >= 3)
    diagonal = sum(
        abs(b[0] - a[0])
        for path in paths
        for a, b in itertools.pairwise(path)
        if abs(b[0] - a[0]) == abs(b[1] - a[1])
    )
    return bends, diagonal


def build(folder: Path) -> None:
    ways: list[dict] = []
    seen: set[int] = set()
    for file in sorted(folder.glob("*.json")):
        category = file.name.split("_")[0]
        for w in json.loads(file.read_text())["elements"]:
            if w.get("type") != "way" or "geometry" not in w or w["id"] in seen:
                continue
            tags = w.get("tags", {})
            if any(k in tags for k in EXCLUDED) or str(tags.get("level", "0")).startswith("-"):
                continue
            if str(tags.get("layer", "0")).startswith("-") or tags.get("room") == "stairs":
                continue
            seen.add(w["id"])
            w["_category"] = category
            ways.append(w)
    found: list[dict] = []
    for group in clusters(ways):
        lat0 = group[0]["geometry"][0]["lat"]
        lon0 = group[0]["geometry"][0]["lon"]
        polygons = [project(w["geometry"], lat0, lon0) for w in group]
        result = raster(polygons)
        if result is None:
            continue
        pixels, _area = result
        graph = Graph.of(thin(pixels))
        graph.prune()
        if not graph.edges:
            continue
        paths = skeleton_paths(graph)
        if not paths:
            continue
        bends, diagonal = interest(paths)
        total = sum(
            abs(b[0] - a[0]) + abs(b[1] - a[1]) for p in paths for a, b in itertools.pairwise(p)
        )
        short = sum(
            max(abs(b[0] - a[0]), abs(b[1] - a[1])) < MIN_SEGMENT
            for path in paths
            for a, b in itertools.pairwise(path)
        )
        if not 2 <= bends <= MAX_BENDS or total < 24 or short > 1:
            continue
        x0 = min(p[0] for path in paths for p in path)
        y0 = min(p[1] for path in paths for p in path)
        found.append(
            {
                "id": f"osm-{min(w['id'] for w in group)}",
                "category": group[0]["_category"],
                "source": sorted(w["id"] for w in group),  # OSM way ids
                "bends": bends,
                "diagonal": diagonal,
                "paths": [[[x - x0, y - y0] for x, y in path] for path in paths],
            }
        )
    # per category the most interesting shapes, about half of them with diagonals
    kept: list[dict] = []
    for category in CATEGORIES:
        mine = sorted((t for t in found if t["category"] == category), key=lambda t: -t["bends"])
        slanted = [t for t in mine if t["diagonal"]][: PER_CATEGORY // 2]
        square = [t for t in mine if not t["diagonal"]][: PER_CATEGORY - len(slanted)]
        kept += slanted + square
        print(f"{category}: {len(mine)} usable, {len(slanted) + len(square)} kept")
    found = kept
    found.sort(key=lambda t: t["id"])
    header = (
        "# Corridor centrelines of real buildings: cells of 0.5 m, eight directions.\n"
        "# Generated by tools/osm_corridors.py from OpenStreetMap indoor mapping (way ids in\n"
        "# `source`). (c) OpenStreetMap contributors, Open Database License 1.0:\n"
        "# https://www.openstreetmap.org/copyright. This file is a derived database and stays\n"
        "# under the ODbL; see docs/third-party.md.\n"
    )
    OUT.write_text(header + yaml.safe_dump(found, default_flow_style=None, width=100))
    print(f"{len(found)} shapes -> {OUT}")


if __name__ == "__main__":
    command, target = sys.argv[1], Path(sys.argv[2])
    if command == "fetch":
        fetch(target)
    else:
        build(target)
