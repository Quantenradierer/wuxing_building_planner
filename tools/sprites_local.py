"""Generate object sprites with the local ComfyUI (comfy-gen) for the modern sprite set.

    uv run python tools/sprites_local.py guides [kind ...]     # draw the img2img guides only
    uv run python tools/sprites_local.py generate [kind ...] [--attempt N]
    uv run python tools/sprites_local.py sheet [kind ...] [--attempt N] --out sheet.png
    uv run python tools/sprites_local.py cut [kind ...]         # picks -> data/sprites/modern/

Each attempt renders `seeds` img2img jobs from the kind's guide (see the prompt file) and
stores them as one 2x2 grid in `.sprites_raw/modern/`, so review, picks and cutting work as
for the Midjourney set (tools/sprites.py). Jobs run strictly one after another: the GPU is
shared and parallel jobs can exhaust the machine's memory.
"""

from __future__ import annotations

import argparse
import fcntl
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from roomplanner.render.theme import Material, Pattern, colour, load_theme

sys.path.insert(0, str(Path(__file__).parent))
import sprites

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "tools" / "sprite_prompts_modern.yaml"
RAW = ROOT / ".sprites_raw" / "modern"
GUIDES = RAW / "guides"
# One GPU job at a time across processes (several studios may run at once).
GPU_LOCK = ROOT / ".sprites_raw" / "gpu.lock"

# SDXL resolutions (width, height) by aspect ratio; the guide canvas is one of these.
BUCKETS = [(1024, 1024), (1152, 896), (1216, 832), (1344, 768), (1536, 640)]
FILL = 0.72  # share of the canvas the object's footprint takes along its longer side
PAPER = (245, 245, 245)


def _use_modern_set() -> None:
    sprites.PICKS = ROOT / "tools" / "sprite_picks_modern.yaml"
    sprites.RAW = RAW
    sprites.OUT = ROOT / "src" / "roomplanner" / "data" / "sprites" / "modern"


def _data() -> dict[str, Any]:
    return yaml.safe_load(PROMPTS.read_text())


def _canvas_size(along: int, deep: int) -> tuple[int, int]:
    ratio = along / deep
    wide = max(ratio, 1 / ratio)
    w, h = min(BUCKETS, key=lambda b: abs(b[0] / b[1] - wide))
    return (w, h) if ratio >= 1 else (h, w)


def _colour(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def draw_guide(kind: str, spec: dict[str, Any]) -> Image.Image:
    along, deep = sprites._sizes()[kind.split(".")[0]]
    size = _canvas_size(along, deep)
    scale = FILL * min(size[0] / along, size[1] / deep)
    w, h = along * scale, deep * scale
    x0, y0 = (size[0] - w) / 2, (size[1] - h) / 2
    short = min(w, h)

    def box(a: float, b: float, c: float, d: float) -> tuple[float, float, float, float]:
        return (x0 + a * w, y0 + b * h, x0 + c * w, y0 + d * h)

    image = Image.new("RGB", size, PAPER)
    draw = ImageDraw.Draw(image)
    base = spec.get("base", ["rect", "#8a8e94", 0.05])
    offset = short * 0.04
    if base[0] != "none":
        shadow = (x0 + offset, y0 + offset, x0 + w + offset, y0 + h + offset)
        footprint = box(0, 0, 1, 1)
        if base[0] == "ellipse":
            draw.ellipse(shadow, fill=(210, 210, 210))
            draw.ellipse(footprint, fill=_colour(base[1]))
        else:
            radius = base[2] * short if len(base) > 2 else 0
            draw.rounded_rectangle(shadow, radius, fill=(210, 210, 210))
            draw.rounded_rectangle(footprint, radius, fill=_colour(base[1]))
    for shape, *args in spec.get("guide", []):
        match shape:
            case "rect":
                radius = args[5] * short * 0.5 if len(args) > 5 else 0
                draw.rounded_rectangle(box(*args[:4]), radius, fill=_colour(args[4]))
            case "ellipse":
                draw.ellipse(box(*args[:4]), fill=_colour(args[4]))
            case "line":
                a, b, c, d = box(*args[:4])
                width = round(args[5] * short)
                draw.line((a, b, c, d), fill=_colour(args[4]), width=width)
                for x, y in ((a, b), (c, d)):
                    r = width / 2
                    draw.ellipse((x - r, y - r, x + r, y + r), fill=_colour(args[4]))
            case _:
                raise ValueError(f"{kind}: unknown guide shape {shape}")
    return image.filter(ImageFilter.GaussianBlur(2))


def guides(kinds: list[str]) -> None:
    subjects = _data()["subjects"]
    GUIDES.mkdir(parents=True, exist_ok=True)
    for kind in kinds:
        path = GUIDES / f"{kind}.png"
        draw_guide(kind, subjects[kind]).save(path)
        print(path)


def _render(
    prompt: str,
    data: dict[str, Any],
    guide: Path,
    seed: int,
    size: tuple[int, int],
    denoise: float,
    transparent: bool = True,
    negative: str | None = None,
    lora: str | None = None,
    model: str = "pony",
) -> Image.Image:
    command = ["comfy-gen", prompt, "--model", model]
    if model in ("pony", "illustrious"):  # SDXL; Flux takes no negative prompt
        command += ["--negative", " ".join((negative or data["negative"]).split())]
    command += [
        "--init",
        str(guide),
        "--denoise",
        str(denoise),
        "--seed",
        str(seed),
        "--width",
        str(size[0]),
        "--height",
        str(size[1]),
        "--prefix",
        "roomplanner",
    ]
    if transparent:
        command.append("--transparent")
    lora = data.get("lora") if lora is None else lora
    if lora:
        command += ["--lora", lora]
    GPU_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with GPU_LOCK.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = subprocess.run(command, capture_output=True, text=True, check=True)
    paths = [Path(line) for line in result.stdout.split() if line.endswith(".png")]
    rgba = [p for p in paths if "-rgba_" in p.name]
    return Image.open((rgba if transparent and rgba else paths)[-1]).convert("RGBA")


def render_grid(
    name: str,
    attempt: int,
    prompt: str,
    init: Image.Image,
    denoise: float,
    data: dict[str, Any],
    transparent: bool = True,
    negative: str | None = None,
    lora: str | None = None,
    model: str = "pony",
) -> bool:
    """Render `seeds` img2img variants of `init` into one 2x2 grid; False if a job failed."""
    out = sprites.RAW / f"{name}.{attempt}.png"
    if out.exists():
        return True
    GUIDES.mkdir(parents=True, exist_ok=True)
    path = GUIDES / f"{name}.png"
    init.save(path)
    tiles: list[Image.Image] = []
    for n in range(data["seeds"]):
        seed = sprites._seed(name, attempt * 10 + n)
        try:
            tiles.append(
                _render(
                    prompt, data, path, seed, init.size, denoise, transparent, negative, lora, model
                )
            )
        except subprocess.CalledProcessError as error:
            print(f"FAILED {name} seed {seed}: {error.stderr[-500:]}", flush=True)
            return False
    grid = Image.new("RGBA", (init.width * 2, init.height * 2), (0, 0, 0, 0))
    for i, tile in enumerate(tiles):
        grid.paste(tile.resize(init.size), ((i % 2) * init.width, (i // 2) * init.height))
    grid.save(out)
    print(f"got {name}", flush=True)
    return True


def _style(data: dict[str, Any], subject: str) -> str:
    return " ".join(data["style"].split()).format(subject=subject)


def generate(kinds: list[str], attempt: int, boost: float = 0.0) -> None:
    """`boost` raises every kind's denoise (capped at 0.95) for more varied attempts."""
    data = _data()
    for kind in kinds:
        spec = data["subjects"][kind]
        prompt = _style(data, spec["prompt"])
        denoise = boosted(spec.get("denoise", data["denoise"]), boost)
        if not render_grid(kind, attempt, prompt, draw_guide(kind, spec), denoise, data):
            return


# --- derived tiers ------------------------------------------------------------------------------
# A prompt file's `derived:` section turns the picked plain sprite of every kind in the file into
# further tiers (low, squatter: the same object worn down) by img2img from that picture instead
# of a guide. Names are `<kind>.<tier>`; the base prompt comes from this file's plain entry, else
# from `derived.source`.


def derived_names(data: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """`<kind>.<tier>` -> (kind, tier) for every derived tier of every kind in the file."""
    tiers = (data.get("derived") or {}).get("tiers") or {}
    kinds = dict.fromkeys(name.split(".")[0] for name in data["subjects"])
    return {f"{kind}.{tier}": (kind, tier) for kind in kinds for tier in tiers}


def base_prompt(data: dict[str, Any], kind: str) -> str:
    if kind in data["subjects"]:
        return data["subjects"][kind]["prompt"]
    source = (data.get("derived") or {}).get("source")
    if source:
        subjects = yaml.safe_load((ROOT / source).read_text())["subjects"]
        if kind in subjects:
            return subjects[kind]["prompt"]
    return kind.replace("_", " ")


def picked_image(kind: str) -> Image.Image | None:
    """The picked variant of `kind` (uncut, on white), or None while nothing is picked."""
    picks = yaml.safe_load(sprites.PICKS.read_text()) or {} if sprites.PICKS.exists() else {}
    if kind not in picks:
        return None
    quadrant, attempt, _, _ = sprites._pick(picks[kind])
    path = sprites.RAW / f"{kind}.{attempt}.png"
    if not path.exists():
        return None
    tile = sprites.quadrants(Image.open(path).convert("RGBA"))[quadrant]
    image = Image.new("RGB", tile.size, (255, 255, 255))
    image.paste(tile, mask=tile.getchannel("A"))
    return image


def derived_prompt(data: dict[str, Any], name: str) -> str:
    kind, tier = derived_names(data)[name]
    template = data["derived"]["tiers"][tier]["prompt"]
    return " ".join(template.split()).format(subject=base_prompt(data, kind))


def derived_denoise(data: dict[str, Any], name: str) -> float:
    """The per-object override from `derived_overrides:`, else the tier's denoise."""
    override = (data.get("derived_overrides") or {}).get(name) or {}
    tier = data["derived"]["tiers"][derived_names(data)[name][1]]
    return override.get("denoise", tier.get("denoise", 0.5))


def boosted(denoise: float, boost: float) -> float:
    return round(min(denoise + boost, 0.95), 2) if boost else denoise


def generate_derived(name: str, attempt: int, boost: float = 0.0) -> None:
    data = _data()
    kind, _ = derived_names(data)[name]
    init = picked_image(kind)
    if init is None:
        raise ValueError(f"{name}: pick a {kind} first")
    denoise = boosted(derived_denoise(data, name), boost)
    render_grid(name, attempt, _style(data, derived_prompt(data, name)), init, denoise, data)


# --- floor textures -----------------------------------------------------------------------------
# A prompt file's `floors:` section makes seamless floor textures `floor.<material>` for the
# materials of `floor_theme`. The guide is that material drawn as the renderer does (colour and
# pattern over the material's `texture_cells`), optionally changed by the entry's `guide:`
# (Material fields); the render is opaque, and the cut blends it into a seamless square tile.
# `floor_style`, `floor_denoise`, `floor_model` (default Flux dev: Pony without the object LoRA
# fills floors with people), `floor_lora` (default none) and `floor_negative` (SDXL models only)
# replace the object settings.


FLOOR_SIDE = 1024  # px, guide and render


def floor_names(data: dict[str, Any]) -> list[str]:
    return list(data.get("floors") or {})


def floor_material(data: dict[str, Any], name: str) -> Material:
    theme = load_theme(data["floor_theme"])
    material = theme.materials[name.removeprefix("floor.")]
    changes = (data["floors"][name] or {}).get("guide") or {}
    return material.model_validate({**material.model_dump(), **changes})


def draw_floor_guide(data: dict[str, Any], name: str) -> Image.Image:
    material = floor_material(data, name)
    fill = colour(material.floor)[:3]
    image = Image.new("RGB", (FLOOR_SIDE, FLOOR_SIDE), fill)
    draw = ImageDraw.Draw(image)
    # Pattern lines several times stronger than in the map, so the model sees the structure.
    *rgb, alpha = colour(material.lines)
    strength = min(1.0, 3 * alpha / 255)
    line = tuple(round(f + (c - f) * strength) for f, c in zip(fill, rgb, strict=True))
    cell = FLOOR_SIDE / material.texture_cells
    width = max(2, round(cell * 0.04))
    n = material.tile
    cells = range(material.texture_cells)
    match material.pattern:
        case Pattern.TILES:
            for i in range(0, material.texture_cells, n):
                draw.line((i * cell, 0, i * cell, FLOOR_SIDE), fill=line, width=width)
                draw.line((0, i * cell, FLOOR_SIDE, i * cell), fill=line, width=width)
        case Pattern.PLANKS:
            for y in cells:
                if y % n == 0:
                    draw.line((0, y * cell, FLOOR_SIDE, y * cell), fill=line, width=width)
                for x in cells:
                    if (x + 3 * (y // n)) % (4 * n) == 0:
                        top, bottom = y * cell, (y + 1) * cell
                        draw.line((x * cell, top, x * cell, bottom), fill=line, width=width)
        case Pattern.GRATE:
            step = cell / 3
            for i in range(round(2 * FLOOR_SIDE / step) + 1):
                offset = i * step
                draw.line((offset, 0, offset - FLOOR_SIDE, FLOOR_SIDE), fill=line, width=width)
        case _:
            pass
    image = image.filter(ImageFilter.GaussianBlur(2))
    # Grain gives img2img some surface to work from; a flat fill comes back flat.
    grain = Image.effect_noise(image.size, 64).convert("RGB")
    return Image.blend(image, ImageChops.overlay(image, grain), 0.5)


def floor_denoise(data: dict[str, Any], name: str) -> float:
    spec = data["floors"][name] or {}
    return spec.get("denoise", data.get("floor_denoise", data["denoise"]))


def generate_floor(name: str, attempt: int, boost: float = 0.0) -> None:
    data = _data()
    spec = data["floors"][name]
    prompt = " ".join(data["floor_style"].split()).format(subject=spec["prompt"])
    denoise = boosted(floor_denoise(data, name), boost)
    guide = draw_floor_guide(data, name)
    negative = data.get("floor_negative")
    lora = data.get("floor_lora", "")  # "": none, the object LoRA draws objects on white
    model = data.get("floor_model", "dev")
    render_grid(name, attempt, prompt, guide, denoise, data, False, negative, lora, model)


def seamless(image: Image.Image) -> Image.Image:
    """Blend the picture with itself shifted by half: the shifted copy wraps at the borders,
    the original covers the middle, so the square tiles without a seam."""
    side = min(image.size)
    left, top = (image.width - side) // 2, (image.height - side) // 2
    image = image.convert("RGB").crop((left, top, left + side, top + side))
    half = side // 2
    shifted = ImageChops.offset(image, half, half)
    ramp = Image.linear_gradient("L").resize((side, side))  # 0 at the top, 255 at the bottom
    tent = ImageChops.darker(ramp, ramp.transpose(Image.Transpose.FLIP_TOP_BOTTOM))
    tent = tent.point(lambda v: min(255, v * 2))  # 0 at the border, 255 in the middle
    mask = ImageChops.darker(tent, tent.transpose(Image.Transpose.ROTATE_90))
    return Image.composite(image, shifted, mask)


def cut_floor(name: str) -> None:
    data = _data()
    picks = yaml.safe_load(sprites.PICKS.read_text()) or {}
    quadrant, attempt, _, _ = sprites._pick(picks[name])
    grid = Image.open(sprites.RAW / f"{name}.{attempt}.png")
    tile = seamless(sprites.quadrants(grid)[quadrant])
    side = floor_material(data, name).texture_cells * sprites.PX_PER_CELL
    sprites.OUT.mkdir(parents=True, exist_ok=True)
    tile.resize((side, side), Image.Resampling.LANCZOS).save(sprites.OUT / f"{name}.png")
    print(f"cut {name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["guides", "generate", "sheet", "cut"])
    parser.add_argument("kinds", nargs="*")
    parser.add_argument("--attempt", type=int, default=0)
    parser.add_argument("--out", type=Path, default=Path("sprite_sheet.png"))
    args = parser.parse_args()
    _use_modern_set()
    kinds = args.kinds or list(_data()["subjects"])
    match args.command:
        case "guides":
            guides(kinds)
        case "generate":
            sprites.RAW.mkdir(parents=True, exist_ok=True)
            generate(kinds, args.attempt)
        case "sheet":
            sprites.sheet(kinds, args.attempt, args.out)
        case _:
            names = args.kinds or list(yaml.safe_load(sprites.PICKS.read_text()) or {})
            floors = set(floor_names(_data()))
            sprites.cut([name for name in names if name not in floors])
            for name in floors & set(names):
                cut_floor(name)


if __name__ == "__main__":
    main()
