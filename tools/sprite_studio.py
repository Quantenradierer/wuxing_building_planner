"""Browser app to generate sprites with the local ComfyUI and pick the best one per object.

    uv run python tools/sprite_studio.py [--set modern] [--prompts FILE] [--port 8765]

Reads the object list and their image-generator descriptions from the prompt file (the format
of tools/sprite_prompts_modern.yaml; whoever writes it, the studio only consumes it). Every
object without a render is queued on start. In the browser each object shows its attempts as
four variants: clicking one writes it to the picks file and cuts the sprite into
data/sprites/<set>/; "Retry" queues another attempt with new seeds. The prompt file is re-read
before every job, so a description can be fixed while the studio runs and then retried.

Tiers in the file's `derived:` section (low, squatter) are not drawn from a guide but worn down
from the picked plain sprite of their kind; they wait for that pick and are queued by it.

"Edit description" changes a designed object's description by hand. "Recreate text
description" asks OpenAI for a new description and writes it into the prompt
file. The key comes from OPENAI_API_KEY or a line `OPENAI_API_KEY=...` in .env (gitignored).

Jobs run strictly one after another (the GPU is shared), through tools/sprites_local.py.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import queue
import re
import sys
import threading
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import yaml
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
import sprites
import sprites_local

ROOT = sprites_local.ROOT
TILE = 320  # px, longer side of a variant in the browser
PAGE = Path(__file__).with_name("sprite_studio.html")
TIER_ORDER = ["", "high", "luxury", "low", "squatter"]

DESCRIBE = """\
You write subject descriptions for an image generator that makes sprites for top-down tabletop
battle maps. The model (Pony Diffusion XL with a top-down map-asset LoRA) renders one object
seen from straight above on a white background, img2img from a rough plan-view drawing.
Write ONE subject description: a single line of 12 to 35 words, comma separated phrases, plain
concrete words for materials, colours and the parts visible from above. The object's back
faces the TOP edge of the picture: say which part of the object is at the top edge. Describe
only the object itself: never a wall, floor, room or surroundings. Colours as plain words, no
hex codes, no measurements or numbers. Never mention camera, view, style or quality tags,
background, perspective, people or text. Answer with the description only."""


class Studio:
    def __init__(self, prompts: Path, picks: Path, model: str) -> None:
        self.prompts = prompts
        self.picks = picks
        self.model = model
        self.jobs: queue.Queue[tuple[str, int]] = queue.Queue()
        self.queued: list[str] = []
        self.rendering: str | None = None
        self.failed: set[str] = set()
        self.lock = threading.Lock()
        self._data: tuple[float, dict[str, Any]] = (0.0, {})

    # --- data -------------------------------------------------------------------------------

    def data(self) -> dict[str, Any]:
        mtime = self.prompts.stat().st_mtime
        if mtime != self._data[0]:
            self._data = (mtime, yaml.safe_load(self.prompts.read_text()))
        return self._data[1]

    def subjects(self) -> dict[str, Any]:
        return self.data()["subjects"]

    def derived(self) -> dict[str, tuple[str, str]]:
        return sprites_local.derived_names(self.data())

    def names(self) -> list[str]:
        """Designed and derived names, grouped by kind: plain, high, luxury, low, squatter."""
        names = list(self.subjects()) + list(self.derived())
        kinds = list(dict.fromkeys(name.split(".")[0] for name in names))

        def order(name: str) -> tuple[int, int]:
            kind, _, tier = name.partition(".")
            rank = TIER_ORDER.index(tier) if tier in TIER_ORDER else len(TIER_ORDER)
            return kinds.index(kind), rank

        return sorted(names, key=order)

    def attempts(self, kind: str) -> list[int]:
        pattern = re.compile(rf"{re.escape(kind)}\.(\d+)\.png")
        found = (pattern.fullmatch(p.name) for p in sprites.RAW.glob(f"{kind}.*.png"))
        return sorted(int(m.group(1)) for m in found if m)

    def read_picks(self) -> dict[str, Any]:
        if not self.picks.exists():
            return {}
        return yaml.safe_load(self.picks.read_text()) or {}

    def state(self) -> dict[str, Any]:
        picks = self.read_picks()
        subjects = self.subjects()
        derived = self.derived()
        kinds: list[dict[str, Any]] = []
        for name in self.names():
            pick = picks.get(name)
            if isinstance(pick, dict):
                pick = {"attempt": pick.get("attempt", 3), "quadrant": pick["quadrant"]}
            base = derived[name][0] if name in derived else None
            status = (
                "rendering"
                if name == self.rendering
                else "queued"
                if name in self.queued
                else "failed"
                if name in self.failed
                else "waiting"
                if base and base not in picks
                else "idle"
            )
            prompt = (
                sprites_local.derived_prompt(self.data(), name)
                if base
                else subjects[name].get("prompt", "")
            )
            denoise = (
                sprites_local.derived_denoise(self.data(), name)
                if base
                else subjects[name].get("denoise", self.data()["denoise"])
            )
            kinds.append(
                {
                    "kind": name,
                    "prompt": prompt,
                    "denoise": denoise,
                    "base": base,
                    "attempts": self.attempts(name),
                    "pick": pick,
                    "status": status,
                }
            )
        return {"kinds": kinds, "queue": len(self.queued), "rendering": self.rendering}

    # --- actions ----------------------------------------------------------------------------

    def enqueue(self, kind: str) -> None:
        derived = self.derived()
        if kind in derived and derived[kind][0] not in self.read_picks():
            return  # waits for the pick of its base kind
        with self.lock:
            if kind in self.queued or kind == self.rendering:
                return
            self.queued.append(kind)
            self.failed.discard(kind)
        self.jobs.put((kind, max(self.attempts(kind), default=0) + 1))

    def enqueue_missing(self) -> None:
        for name in self.names():
            if not self.attempts(name):
                self.enqueue(name)

    def pick(self, kind: str, attempt: int, quadrant: int) -> None:
        with self.lock:
            picks = self.read_picks()
            picks[kind] = {"quadrant": quadrant, "attempt": attempt}
            header = [
                line
                for line in (self.picks.read_text() if self.picks.exists() else "").splitlines()
                if line.startswith("#")
            ]
            lines = header + [f"{name}: {_flow(entry)}" for name, entry in picks.items()]
            self.picks.write_text("\n".join(lines) + "\n")
            sprites.cut([kind])
        # The worn-down tiers of this kind can start now.
        for name, (base, _) in self.derived().items():
            if base == kind and not self.attempts(name):
                self.enqueue(name)

    def describe(self, kind: str, hint: str = "") -> str:
        """Ask OpenAI for a new description of `kind` and write it into the prompt file."""
        data = self.data()
        spec = data["subjects"][kind]
        along, deep = sprites._sizes()[kind.split(".")[0]]
        tier = kind.partition(".")[2] or "middle"
        siblings = [
            f"- {name}: {spec.get('prompt', '')}"
            for name, spec in data["subjects"].items()
            if name.split(".")[0] == kind.split(".")[0] and name != kind
        ]
        request = "\n".join(
            [
                f"Object kind: {kind.split('.')[0].replace('_', ' ')}",
                f"Wealth tier: {tier}",
                f"Footprint: {along * 0.5:g} m wide x {deep * 0.5:g} m deep",
                f"Look of the whole set: {data.get('look', 'not specified')}",
                f"Current description (write a different, better one): "
                f"{data['subjects'][kind].get('prompt', '')}",
                *(["Other tiers of the same object, for contrast:", *siblings] if siblings else []),
                f"Colours of the rough guide drawing the image starts from: {_guide_colours(spec)}",
                *([f"Guidance: {hint}"] if hint else []),
            ]
        )
        text = _openai(self.model, DESCRIBE, request).strip().strip('"').replace("\n", " ")
        with self.lock:
            _set_prompt(self.prompts, kind, text)
        return text

    def set_prompt(self, kind: str, text: str, denoise: float | None = None) -> None:
        """Replace the description (and optionally the denoise) of a designed kind."""
        if kind not in self.subjects():
            raise ValueError(f"{kind}: only designed objects have their own description")
        text = " ".join(text.split())
        if not text:
            raise ValueError("empty description")
        if denoise is not None and not 0.1 <= denoise <= 1.0:
            raise ValueError("denoise must be between 0.1 and 1.0")
        with self.lock:
            _set_prompt(self.prompts, kind, text)
            if denoise is not None:
                _set_denoise(self.prompts, kind, denoise)

    def set_denoise(self, kind: str, denoise: float) -> None:
        """Denoise of one object: designed in its subject entry, derived as an override."""
        if not 0.1 <= denoise <= 1.0:
            raise ValueError("denoise must be between 0.1 and 1.0")
        with self.lock:
            if kind in self.subjects():
                _set_denoise(self.prompts, kind, denoise)
            elif kind in self.derived():
                _set_override(self.prompts, kind, denoise)
            else:
                raise ValueError(f"unknown object {kind}")

    def work(self) -> None:
        while True:
            kind, attempt = self.jobs.get()
            with self.lock:
                self.queued.remove(kind)
                self.rendering = kind
            try:
                if kind in self.derived():
                    sprites_local.generate_derived(kind, attempt)
                else:
                    sprites_local.generate([kind], attempt)
            except Exception as error:  # a broken prompt entry must not stop the queue
                print(f"FAILED {kind}: {error}", flush=True)
            with self.lock:
                self.rendering = None
                if not (sprites.RAW / f"{kind}.{attempt}.png").exists():
                    self.failed.add(kind)

    # --- images -----------------------------------------------------------------------------

    def tile(self, kind: str, attempt: int, quadrant: int) -> bytes:
        grid = Image.open(sprites.RAW / f"{kind}.{attempt}.png")
        image = sprites.quadrants(grid)[quadrant]
        image.thumbnail((TILE, TILE))
        return _png(image)

    def guide(self, kind: str) -> bytes:
        """The drawing a designed kind's img2img starts from."""
        image = sprites_local.draw_guide(kind, self.subjects()[kind])
        image.thumbnail((TILE, TILE))
        return _png(image)

    def sprite(self, kind: str) -> bytes:
        image = Image.open(sprites.OUT / f"{kind}.png")
        image.thumbnail((TILE, TILE))
        return _png(image)


def _flow(entry: Any) -> str:
    if isinstance(entry, dict):
        return "{" + ", ".join(f"{k}: {v}" for k, v in entry.items()) + "}"
    return str(entry)


def _guide_colours(spec: dict[str, Any]) -> str:
    base = spec.get("base", ["rect", "#8a8e94"])
    shapes = [base] if base[0] != "none" else []
    shapes += spec.get("guide", [])
    colours = [v for shape in shapes for v in shape if isinstance(v, str) and v.startswith("#")]
    return ", ".join(dict.fromkeys(colours)) or "grey"


def _set_prompt(path: Path, kind: str, text: str) -> None:
    """Replace the `prompt:` line of one subject in place, keeping the file's layout."""
    lines = path.read_text().splitlines(keepends=True)
    start = lines.index(f"  {kind}:\n")
    for i in range(start + 1, len(lines)):
        if not lines[i].startswith("    "):
            break
        if lines[i].startswith("    prompt:"):
            lines[i] = f"    prompt: {json.dumps(text)}\n"
            path.write_text("".join(lines))
            return
    raise ValueError(f"{kind}: no prompt line")


def _set_denoise(path: Path, kind: str, value: float) -> None:
    """Set one subject's `denoise:` line in place, adding it when the subject has none."""
    lines = path.read_text().splitlines(keepends=True)
    start = lines.index(f"  {kind}:\n")
    end = start + 1
    while end < len(lines) and lines[end].startswith("    "):
        end += 1
    line = f"    denoise: {round(value, 2):g}\n"
    for i in range(start + 1, end):
        if lines[i].startswith("    denoise:"):
            lines[i] = line
            break
    else:
        lines.insert(start + 1, line)
    path.write_text("".join(lines))


def _set_override(path: Path, name: str, value: float) -> None:
    """Set a derived object's denoise in the file's `derived_overrides:` map (made on demand)."""
    lines = path.read_text().splitlines(keepends=True)
    line = f"  {name}: {{denoise: {round(value, 2):g}}}\n"
    if "derived_overrides:\n" not in lines:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines += ["\n", "# Per-object denoise of derived tiers, set in the sprite studio.\n"]
        lines.append("derived_overrides:\n")
    start = lines.index("derived_overrides:\n")
    end = start + 1
    while end < len(lines) and lines[end].startswith("  "):
        if lines[end].startswith(f"  {name}:"):
            lines[end] = line
            break
        end += 1
    else:
        lines.insert(end, line)
    path.write_text("".join(lines))


def _api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "OPENAI_API_KEY":
                key = value.strip().strip('"').strip("'")
    if not key:
        raise RuntimeError("no OpenAI key: set OPENAI_API_KEY or put it into .env")
    return key


def _openai(model: str, system: str, user: str) -> str:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.load(response)["choices"][0]["message"]["content"]


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def handler(studio: Studio) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _send(self, body: bytes, kind: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store" if "json" in kind else "max-age=3600")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parts = [unquote(p) for p in urlparse(self.path).path.strip("/").split("/")]
            try:
                match parts:
                    case [""]:
                        self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
                    case ["api", "state"]:
                        self._send(json.dumps(studio.state()).encode(), "application/json")
                    case ["tile", kind, attempt, quadrant]:
                        body = studio.tile(kind, int(attempt), int(quadrant))
                        self._send(body, "image/png")
                    case ["guide", kind]:
                        self._send(studio.guide(kind), "image/png")
                    case ["sprite", kind]:
                        self._send(studio.sprite(kind), "image/png")
                    case _:
                        self._send(b"not found", "text/plain", HTTPStatus.NOT_FOUND)
            except FileNotFoundError:
                self._send(b"not found", "text/plain", HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            match urlparse(self.path).path:
                case "/api/retry":
                    studio.enqueue(data["kind"])
                case "/api/pick":
                    studio.pick(data["kind"], int(data["attempt"]), int(data["quadrant"]))
                case "/api/prompt":
                    try:
                        value = data.get("denoise")
                        denoise = None if value is None else float(value)
                        studio.set_prompt(data["kind"], data["prompt"], denoise)
                    except ValueError as error:
                        body = json.dumps({"error": str(error)}).encode()
                        self._send(body, "application/json", HTTPStatus.BAD_REQUEST)
                        return
                case "/api/denoise":
                    try:
                        studio.set_denoise(data["kind"], float(data["denoise"]))
                    except ValueError as error:
                        body = json.dumps({"error": str(error)}).encode()
                        self._send(body, "application/json", HTTPStatus.BAD_REQUEST)
                        return
                case "/api/describe":
                    try:
                        text = studio.describe(data["kind"], data.get("hint", ""))
                    except Exception as error:  # shown in the browser
                        body = json.dumps({"error": str(error)}).encode()
                        self._send(body, "application/json", HTTPStatus.BAD_GATEWAY)
                        return
                    self._send(json.dumps({"prompt": text}).encode(), "application/json")
                    return
                case _:
                    self._send(b"not found", "text/plain", HTTPStatus.NOT_FOUND)
                    return
            self._send(b"{}", "application/json")

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", default="modern", help="sprite set name")
    parser.add_argument("--prompts", type=Path, help="default tools/sprite_prompts_<set>.yaml")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--openai-model", default=os.environ.get("OPENAI_MODEL", "gpt-5-mini"))
    args = parser.parse_args()

    prompts = args.prompts or ROOT / "tools" / f"sprite_prompts_{args.set}.yaml"
    sprites_local.PROMPTS = prompts.resolve()
    sprites_local.RAW = sprites.RAW = ROOT / ".sprites_raw" / args.set
    sprites_local.GUIDES = sprites.RAW / "guides"
    sprites.PICKS = ROOT / "tools" / f"sprite_picks_{args.set}.yaml"
    sprites.OUT = ROOT / "src" / "roomplanner" / "data" / "sprites" / args.set
    sprites.RAW.mkdir(parents=True, exist_ok=True)

    studio = Studio(sprites_local.PROMPTS, sprites.PICKS, args.openai_model)
    studio.enqueue_missing()
    threading.Thread(target=studio.work, daemon=True).start()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(studio))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"sprite studio on {url} ({len(studio.queued)} objects queued)", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
