"""Browser app to generate sprites with the local ComfyUI and pick the best one per object.

    uv run python tools/sprite_studio.py [--set modern] [--prompts FILE] [--port 8765]

Reads the object list and their image-generator descriptions from the prompt file (the format
of tools/sprite_prompts_modern.yaml; whoever writes it, the studio only consumes it). Every
object without a render is queued on start. In the browser each object shows its attempts as
four variants: clicking one writes it to the picks file and cuts the sprite into
data/sprites/<set>/; "Retry" queues another attempt with new seeds. The prompt file is re-read
before every job, so a description can be fixed while the studio runs and then retried.

Jobs run strictly one after another (the GPU is shared), through tools/sprites_local.py.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import queue
import re
import sys
import threading
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


class Studio:
    def __init__(self, prompts: Path, picks: Path) -> None:
        self.prompts = prompts
        self.picks = picks
        self.jobs: queue.Queue[tuple[str, int]] = queue.Queue()
        self.queued: list[str] = []
        self.rendering: str | None = None
        self.failed: set[str] = set()
        self.lock = threading.Lock()
        self._subjects: tuple[float, dict[str, Any]] = (0.0, {})

    # --- data -------------------------------------------------------------------------------

    def subjects(self) -> dict[str, Any]:
        mtime = self.prompts.stat().st_mtime
        if mtime != self._subjects[0]:
            self._subjects = (mtime, yaml.safe_load(self.prompts.read_text())["subjects"])
        return self._subjects[1]

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
        kinds: list[dict[str, Any]] = []
        for kind, spec in subjects.items():
            pick = picks.get(kind)
            if isinstance(pick, dict):
                pick = {"attempt": pick.get("attempt", 3), "quadrant": pick["quadrant"]}
            status = (
                "rendering"
                if kind == self.rendering
                else "queued"
                if kind in self.queued
                else "failed"
                if kind in self.failed
                else "idle"
            )
            kinds.append(
                {
                    "kind": kind,
                    "prompt": spec.get("prompt", ""),
                    "attempts": self.attempts(kind),
                    "pick": pick,
                    "status": status,
                }
            )
        return {"kinds": kinds, "queue": len(self.queued), "rendering": self.rendering}

    # --- actions ----------------------------------------------------------------------------

    def enqueue(self, kind: str) -> None:
        with self.lock:
            if kind in self.queued or kind == self.rendering:
                return
            self.queued.append(kind)
            self.failed.discard(kind)
        self.jobs.put((kind, max(self.attempts(kind), default=0) + 1))

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

    def work(self) -> None:
        while True:
            kind, attempt = self.jobs.get()
            with self.lock:
                self.queued.remove(kind)
                self.rendering = kind
            try:
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

    def sprite(self, kind: str) -> bytes:
        image = Image.open(sprites.OUT / f"{kind}.png")
        image.thumbnail((TILE, TILE))
        return _png(image)


def _flow(entry: Any) -> str:
    if isinstance(entry, dict):
        return "{" + ", ".join(f"{k}: {v}" for k, v in entry.items()) + "}"
    return str(entry)


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
    args = parser.parse_args()

    prompts = args.prompts or ROOT / "tools" / f"sprite_prompts_{args.set}.yaml"
    sprites_local.PROMPTS = prompts.resolve()
    sprites_local.RAW = sprites.RAW = ROOT / ".sprites_raw" / args.set
    sprites_local.GUIDES = sprites.RAW / "guides"
    sprites.PICKS = ROOT / "tools" / f"sprite_picks_{args.set}.yaml"
    sprites.OUT = ROOT / "src" / "roomplanner" / "data" / "sprites" / args.set
    sprites.RAW.mkdir(parents=True, exist_ok=True)

    studio = Studio(sprites_local.PROMPTS, sprites.PICKS)
    for kind in studio.subjects():
        if not studio.attempts(kind):
            studio.enqueue(kind)
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
