"""Local web UI: a form with all generation parameters and a floor viewer with layers.

The server only generates and renders: `POST /api/generate` returns the building's JSON
(the contract, see serialization.py) plus the themed image of every floor. The page draws
its overlay layers (rooms, labels, doors, objects, devices, lights, grid) from that JSON.
`POST /api/export/foundry` takes the same request and returns the building as one
`.schattenakte.json` for the Schattenakte Foundry module (see export/foundry.py).
"""

from __future__ import annotations

import base64
import io
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any, cast

from PIL import Image
from pydantic import ValidationError

from roomplanner.errors import RoomplannerError
from roomplanner.export.common import ExportOptions
from roomplanner.export.foundry import to_foundry
from roomplanner.generator import generate
from roomplanner.geometry import Side
from roomplanner.params import (
    BuildingType,
    Condition,
    EntranceKind,
    GenerationParams,
    Security,
    Shape,
    Wealth,
)
from roomplanner.render.image import DEFAULT_CELL_PX as VTT_CELL_PX
from roomplanner.render.image import RenderOptions, render_floor
from roomplanner.render.theme import load_theme
from roomplanner.rules import load_rules
from roomplanner.serialization import to_dict

type JsonObject = dict[str, Any]

PADDING = 2  # cells around the building in the images; the page needs it for its overlays
DEFAULT_CELL_PX = 24  # smaller than the CLI's default: fast enough for clicking through seeds
MAX_CELL_PX = 100
_DATA = resources.files("roomplanner") / "data"


class RequestError(ValueError):
    """The request is malformed (as opposed to a building that can't be generated)."""


def options() -> JsonObject:
    """Choices for the page's form."""
    return {
        "building_types": [t.value for t in BuildingType],
        "wealth": [w.value for w in Wealth],
        "condition": [c.value for c in Condition],
        "security": [s.value for s in Security],
        "shape": [s.value for s in Shape],
        "sides": [s.value for s in Side],
        "entrances": [e.value for e in EntranceKind],
        # Room types per building type, for generating a lone room.
        "rooms": {t.value: sorted(load_rules(t).rooms) for t in BuildingType},
        "themes": sorted(f.name.removesuffix(".yaml") for f in (_DATA / "themes").iterdir()),
        "defaults": {"theme": "neon", "cell_px": DEFAULT_CELL_PX},
    }


def generate_response(request: JsonObject) -> JsonObject:
    """Generate and render a building for the page. Raises RequestError, RoomplannerError."""
    params = _params(request)
    cell_px = request.get("cell_px", DEFAULT_CELL_PX)
    if not isinstance(cell_px, int) or not 4 <= cell_px <= MAX_CELL_PX:
        raise RequestError(f"cell_px must be 4..{MAX_CELL_PX}")
    theme = load_theme(str(request.get("theme", "neon")))
    building = generate(params)
    render = RenderOptions(cell_px, padding=PADDING, lighting=bool(request.get("lighting", True)))
    images = {
        str(floor.level): _data_url(render_floor(building, floor, theme, render))
        for floor in building.floors
    }
    return {"building": to_dict(building), "images": images, "padding": PADDING}


def foundry_export(request: JsonObject) -> tuple[str, bytes]:
    """The requested building as (file name, `.schattenakte.json` body). Uses the CLI's VTT
    defaults (finer images, the VTT does the lighting), not the page's preview settings.
    """
    params = _params(request)
    theme = load_theme(str(request.get("theme", "neon")))
    building = generate(params)
    name = f"{building.params.building_type}_{building.seed}"
    export = to_foundry(building, theme, ExportOptions(cell_px=VTT_CELL_PX), name)
    return f"{name}.schattenakte.json", export.json().encode()


def _params(request: JsonObject) -> GenerationParams:
    raw = request.get("params")
    if not isinstance(raw, dict):
        raise RequestError("missing params")
    try:
        return GenerationParams.model_validate(raw)
    except ValidationError as error:
        raise RequestError(_validation_message(error)) from error


def _data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, "WEBP", quality=90)
    return "data:image/webp;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _validation_message(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in e['loc']) or 'params'}: {e['msg']}"
        for e in error.errors()
    )


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            page = (resources.files("roomplanner.ui") / "index.html").read_bytes()
            self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
        elif self.path == "/api/options":
            self._json(HTTPStatus.OK, options())
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path not in ("/api/generate", "/api/export/foundry"):
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(request, dict):
                raise RequestError("expected a JSON object")
            request = cast(JsonObject, request)
            if self.path == "/api/generate":
                self._json(HTTPStatus.OK, generate_response(request))
            else:
                name, body = foundry_export(request)
                disposition = f'attachment; filename="{name}"'
                self._send(HTTPStatus.OK, body, "application/json", disposition)
        except (RequestError, json.JSONDecodeError) as error:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except RoomplannerError as error:
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})

    def _json(self, status: HTTPStatus, body: JsonObject) -> None:
        self._send(status, json.dumps(body).encode(), "application/json")

    def _send(
        self, status: HTTPStatus, body: bytes, content_type: str, disposition: str | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def make_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _Handler)
