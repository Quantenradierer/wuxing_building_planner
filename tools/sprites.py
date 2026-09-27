"""Generate object sprites with Midjourney and cut them out for the image renderer.

    set -a; . ../SINner/.env; set +a            # MJ_* keys
    uv run python tools/sprites.py generate [kind ...] [--attempt N]
    uv run python tools/sprites.py sheet [kind ...]        # contact sheets to review
    uv run python tools/sprites.py cut [kind ...]          # picks -> data/sprites/<set>/

A name `<kind>.<wealth>` (e.g. `bed.high`) is a wealth variant from `variants:` in the
prompt file, with its own style and style reference; it is cut to `<kind>.<wealth>.png`.

Workflow, conventions and prompting rules: docs/sprites.md.

`generate` stores the raw 2x2 grids in `.sprites_raw/` (not versioned). `tools/sprite_picks.yaml`
names the chosen quadrant per kind (0..3, reading order), optionally with the attempt and
a rotation in degrees counter-clockwise to bring the back of the object to the top.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import time
import zlib
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
import midjourney as mj

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "tools" / "sprite_prompts.yaml"
PICKS = ROOT / "tools" / "sprite_picks.yaml"
RAW = ROOT / ".sprites_raw"
OBJECTS = ROOT / "src" / "roomplanner" / "data" / "objects.yaml"
OUT = ROOT / "src" / "roomplanner" / "data" / "sprites" / "midjourney"

PX_PER_CELL = 96  # stored resolution; the renderer draws at up to 100 px per cell
MAX_SIDE = 640
BATCH = 6  # concurrent Midjourney jobs


def _sizes() -> dict[str, tuple[int, int]]:
    data = yaml.safe_load(OBJECTS.read_text())
    return {kind: tuple(spec["size"]) for kind, spec in data["objects"].items()}


def _prompts() -> dict[str, str]:
    """The full prompt (without seed) per sprite name: `<kind>` or `<kind>.<wealth>`."""
    data = yaml.safe_load(PROMPTS.read_text())
    prompts = _prompt_set(data, data["subjects"], "")
    for tier, variant in data.get("variants", {}).items():
        prompts |= _prompt_set({**data, **variant}, variant["subjects"], f".{tier}")
    return prompts


def _prompt_set(data: dict[str, Any], subjects: dict[str, str], suffix_name: str) -> dict[str, str]:
    sizes = _sizes()
    style = " ".join(data["style"].split())
    sref = ""
    if data.get("sref"):
        sref = f" --sref {data['sref']} --sw {data.get('sref_weight', 100)}"
    prompts: dict[str, str] = {}
    for kind, subject in subjects.items():
        suffix = data["suffix"]
        if kind in data.get("exclude", {}):
            suffix = suffix.replace("--no ", f"--no {data['exclude'][kind]}, ", 1)
        prompts[kind + suffix_name] = (
            f"{style.format(subject=subject)} {_aspect(sizes[kind])} {suffix}{sref}"
        )
    return prompts


def _seed(kind: str, attempt: int) -> int:
    return zlib.crc32(f"roomplanner:{kind}:{attempt}".encode()) % 4_000_000_000


def _raw_path(kind: str, attempt: int) -> Path:
    return RAW / f"{kind}.{attempt}.png"


def _aspect(size: tuple[int, int]) -> str:
    along, deep = size
    # Midjourney wants integers; thin objects get at most 3:1 (they are stretched anyway).
    ratio = max(1 / 3, min(3, along / deep))
    if ratio >= 1:
        return f"--ar {round(ratio * 2)}:2"
    return f"--ar 2:{round(2 / ratio)}"


def _fresh_style_references(prompts: dict[str, str]) -> dict[str, str]:
    """Re-sign the --sref links: they are Discord CDN links that expire after a day."""
    links = {m for p in prompts.values() for m in re.findall(r"--sref (\S+)", p)}
    fresh = {link: mj.refresh_url(link) for link in links}
    return {
        name: re.sub(r"--sref (\S+)", lambda m: f"--sref {fresh[m.group(1)]}", prompt)
        for name, prompt in prompts.items()
    }


def generate(kinds: list[str], attempt: int) -> None:
    prompts = _fresh_style_references(_prompts())
    RAW.mkdir(exist_ok=True)
    todo = [k for k in kinds if not _raw_path(k, attempt).exists()]
    for start in range(0, len(todo), BATCH):
        batch = todo[start : start + BATCH]
        seeds = {_seed(k, attempt): k for k in batch}
        for seed, kind in seeds.items():
            mj.imagine(f"{prompts[kind]} --seed {seed}")
            print(f"sent {kind} ({seed})", flush=True)
            time.sleep(2)
        found = mj.wait_for_grids(list(seeds), timeout=900)
        for seed, url in found.items():
            _raw_path(seeds[seed], attempt).write_bytes(_to_png(mj.download(url)))
            print(f"got {seeds[seed]}", flush=True)
        for seed in set(seeds) - set(found):
            print(f"MISSING {seeds[seed]} ({seed})", flush=True)
        if not found:
            # Nothing at all came back: most likely the Midjourney limit. Rerun later;
            # finished grids are skipped.
            print("STOP: empty batch (limit reached?)", flush=True)
            return


def _to_png(data: bytes) -> bytes:
    buffer = io.BytesIO()
    Image.open(io.BytesIO(data)).convert("RGB").save(buffer, "PNG")
    return buffer.getvalue()


def quadrants(grid: Image.Image) -> list[Image.Image]:
    w, h = grid.size[0] // 2, grid.size[1] // 2
    return [grid.crop((x, y, x + w, y + h)) for y in (0, h) for x in (0, w)]


def _background(image: Image.Image) -> tuple[int, int, int]:
    """Median colour of the image border."""
    w, h = image.size
    border = [image.getpixel((x, y)) for x in range(0, w, 4) for y in (0, h - 1)]
    border += [image.getpixel((x, y)) for y in range(0, h, 4) for x in (0, w - 1)]
    channels = zip(*border, strict=True)  # type: ignore[arg-type]
    return tuple(sorted(c)[len(c) // 2] for c in map(list, channels))  # type: ignore[return-value]


def _not_background_hue(image: Image.Image, background: tuple[int, int, int]) -> Image.Image:
    """Mask (255 = keep) without pixels of the background's hue: its shadows and spill.

    Only for a saturated background colour, e.g. magenta for pale objects.
    """
    top = max(background)
    target = [c / top for c in background]
    mask = Image.new("L", image.size, 255)
    pixels = image.load()
    out = mask.load()
    assert pixels is not None and out is not None
    for y in range(image.height):
        for x in range(image.width):
            r, g, b = pixels[x, y]  # type: ignore[misc]
            peak = max(r, g, b)
            if peak < 40:
                continue
            if max(abs(c / peak - t) for c, t in zip((r, g, b), target, strict=True)) < 0.4:
                out[x, y] = 0
    return mask


def cut_out(image: Image.Image, tolerance: int = 70) -> Image.Image:
    """Key out the background connected to the border, trim to the object."""
    small = image.convert("RGB")
    scale = 768 / max(small.size)
    if scale < 1:
        small = small.resize((round(small.width * scale), round(small.height * scale)))
    # Distance to the background colour (max over channels), smoothed against grain.
    background = _background(small)
    bg = Image.new("RGB", small.size, background)
    diff = ImageChops.difference(small, bg).split()
    distance = ImageChops.lighter(ImageChops.lighter(diff[0], diff[1]), diff[2])
    distance = distance.filter(ImageFilter.MedianFilter(3)).point(lambda v: min(v, 254))
    # Flood from a frame of zero distance with 255 (no real pixel has it): the background.
    framed = Image.new("L", (small.width + 2, small.height + 2), 0)
    framed.paste(distance, (1, 1))
    ImageDraw.floodfill(framed, (0, 0), 255, thresh=tolerance)
    alpha = framed.point(lambda v: 0 if v == 255 else 255)
    alpha = alpha.crop((1, 1, small.width + 1, small.height + 1))
    if max(background) - min(background) > 100:
        alpha = ImageChops.multiply(alpha, _not_background_hue(small, background))
    # Shrink by a pixel against white halos, then soften the edge.
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.7))
    sprite = small.convert("RGBA")
    sprite.putalpha(alpha)
    box = alpha.point(lambda v: 255 if v > 32 else 0).getbbox()
    return sprite.crop(box) if box else sprite


def _pick(entry: int | dict[str, int]) -> tuple[int, int, int, bool]:
    if isinstance(entry, int):
        return entry, 3, 0, False
    fit = bool(entry.get("fit", False))
    return entry["quadrant"], entry.get("attempt", 3), entry.get("rotate", 0), fit


def cut(kinds: list[str]) -> None:
    picks: dict[str, int | dict[str, int]] = yaml.safe_load(PICKS.read_text()) or {}
    sizes = _sizes()
    OUT.mkdir(parents=True, exist_ok=True)
    for kind in kinds:
        if kind not in picks:
            continue
        quadrant, attempt, rotate, fit = _pick(picks[kind])
        grid = Image.open(_raw_path(kind, attempt))
        sprite = cut_out(quadrants(grid)[quadrant])
        if rotate:
            sprite = sprite.rotate(rotate, expand=True)
        along, deep = sizes[kind.split(".")[0]]
        w, h = along * PX_PER_CELL, deep * PX_PER_CELL
        scale = min(1.0, MAX_SIDE / max(w, h))
        size = (round(w * scale), round(h * scale))
        if fit:  # keep the picture's shape, centred on a transparent canvas
            canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            sprite.thumbnail(size, Image.Resampling.LANCZOS)
            canvas.paste(sprite, ((size[0] - sprite.width) // 2, (size[1] - sprite.height) // 2))
            sprite = canvas
        else:
            sprite = sprite.resize(size, Image.Resampling.LANCZOS)
        sprite.save(OUT / f"{kind}.png", optimize=True)
        print(f"cut {kind}")


def sheet(kinds: list[str], attempt: int, out: Path) -> None:
    """One row per kind: the four quadrants cut out, on a dark background, numbered."""
    tile = 220
    kinds = [k for k in kinds if _raw_path(k, attempt).exists()]
    image = Image.new("RGB", (tile * 4 + 180, tile * len(kinds)), (30, 32, 40))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(18)
    for row, kind in enumerate(kinds):
        draw.text((6, row * tile + 8), kind, fill=(230, 230, 230), font=font)
        for i, quad in enumerate(quadrants(Image.open(_raw_path(kind, attempt)))):
            sprite = cut_out(quad)
            sprite.thumbnail((tile - 16, tile - 16))
            x, y = 180 + i * tile + 8, row * tile + 8
            image.paste(sprite, (x, y), sprite)
            draw.text((x, y), str(i), fill=(255, 200, 0), font=font)
    image.save(out)
    print(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["generate", "sheet", "cut"])
    parser.add_argument("kinds", nargs="*")
    parser.add_argument("--attempt", type=int, default=0)
    parser.add_argument("--out", type=Path, default=Path("sprite_sheet.png"))
    args = parser.parse_args()
    kinds = args.kinds or [name for name in _prompts() if "." not in name]
    match args.command:
        case "generate":
            generate(kinds, args.attempt)
        case "sheet":
            sheet(kinds, args.attempt, args.out)
        case _:
            cut(args.kinds or list(yaml.safe_load(PICKS.read_text()) or {}))


if __name__ == "__main__":
    main()
