"""Minimal Midjourney client over Discord, modelled on SINner's image_generation service.

Needs MJ_SERVER_ID, MJ_CHANNEL_ID and MJ_PRIVATE_DISCORD_TOKEN in the environment.
A prompt is sent as an /imagine interaction with a unique `--seed`; the finished 2x2 grid is
found again in the channel by that seed.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from typing import Any

MIDJOURNEY_BOT_ID = "936929561302675456"
_API = "https://discord.com/api/v9"
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0"


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is not set")
    return value


def _request(url: str, payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": _env("MJ_PRIVATE_DISCORD_TOKEN"),
            "Content-Type": "application/json",
            "User-Agent": _UA,
        },
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
    return json.loads(body) if body else None


def imagine(prompt: str) -> None:
    command = {
        "id": "938956540159881230",
        "application_id": MIDJOURNEY_BOT_ID,
        "version": "1237876415471554623",
        "default_member_permissions": None,
        "type": 1,
        "nsfw": False,
        "name": "imagine",
        "description": "Create images with Midjourney",
        "dm_permission": True,
        "contexts": [0, 1, 2],
        "integration_types": [0, 1],
        "options": [
            {"type": 3, "name": "prompt", "description": "The prompt to imagine", "required": True}
        ],
    }
    _request(
        f"{_API}/interactions",
        {
            "type": 2,
            "application_id": MIDJOURNEY_BOT_ID,
            "guild_id": _env("MJ_SERVER_ID"),
            "channel_id": _env("MJ_CHANNEL_ID"),
            "session_id": "38985802",
            "data": {
                "version": command["version"],
                "id": command["id"],
                "name": "imagine",
                "type": 1,
                "options": [{"type": 3, "name": "prompt", "value": prompt}],
                "application_command": command,
                "attachments": [],
            },
        },
    )


def latest_messages(limit: int = 100) -> list[dict[str, Any]]:
    return _request(f"{_API}/channels/{_env('MJ_CHANNEL_ID')}/messages?limit={limit}")


def find_grid(messages: list[dict[str, Any]], seed: int) -> str | None:
    """URL of the finished grid for `seed`; in-progress messages carry a percentage."""
    for message in messages:
        if message.get("author", {}).get("id") != MIDJOURNEY_BOT_ID:
            continue
        content = message.get("content", "")
        if not re.search(rf"--seed {seed}(?!\d)", content) or re.search(r"\(\d+%\)", content):
            continue
        for attachment in message.get("attachments", []):
            if str(attachment.get("content_type", "")).startswith("image/"):
                return attachment["url"]
    return None


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def wait_for_grids(seeds: list[int], timeout: float = 600, poll: float = 10) -> dict[int, str]:
    """Poll the channel until every seed has a finished grid (or the timeout passes)."""
    found: dict[int, str] = {}
    deadline = time.monotonic() + timeout
    while len(found) < len(seeds) and time.monotonic() < deadline:
        time.sleep(poll)
        messages = latest_messages()
        for seed in seeds:
            if seed not in found and (url := find_grid(messages, seed)):
                found[seed] = url
    return found
