"""Texts in the user's language: English (the language of the ids) and German.

Language is a presentation option, not part of the generated building: the model and the JSON
stay language-neutral (ids such as `exam_room`), and the renderers, exports and the web UI
translate them with this module. The catalogues live in `data/i18n/<language>.yaml`; an id
without a translation falls back to itself with the underscores turned into spaces.
"""

from __future__ import annotations

from enum import StrEnum
from functools import cache
from importlib import resources
from typing import Any, cast

import yaml


class Language(StrEnum):
    EN = "en"
    DE = "de"


type Catalogue = dict[str, Any]


@cache
def catalogue(language: Language) -> Catalogue:
    """The raw catalogue of a language: `rooms`, `objects`, `terms`, `ui`."""
    path = resources.files("roomplanner") / "data" / "i18n" / f"{language.value}.yaml"
    return cast(Catalogue, yaml.safe_load(path.read_text(encoding="utf-8")))


def humanize(identifier: str) -> str:
    return identifier.replace("_", " ")


def _lookup(language: Language, *path: str) -> str | None:
    node: Any = catalogue(language)
    for part in path:
        if not isinstance(node, dict):
            return None
        node = cast(dict[str, Any], node).get(part)
    return node if isinstance(node, str) else None


def room_name(room_type: str, language: Language = Language.EN) -> str:
    return _lookup(language, "rooms", room_type) or humanize(room_type)


def object_name(kind: str, language: Language = Language.EN) -> str:
    return _lookup(language, "objects", kind) or humanize(kind)


def term(group: str, key: str, language: Language = Language.EN) -> str:
    """A parameter value (`building_type`, `wealth`, …) or word (`word`) in the language."""
    return _lookup(language, "terms", group, key) or humanize(key)


def level_name(level: int, language: Language = Language.EN) -> str:
    if level == 0:
        return term("floor", "ground", language)
    if level > 0:
        return _lookup(language, "terms", "floor", "above").format(n=level)  # pyright: ignore[reportOptionalMemberAccess]
    return _lookup(language, "terms", "floor", "basement").format(n=-level)  # pyright: ignore[reportOptionalMemberAccess]


def ui_texts(language: Language) -> dict[str, str]:
    """All UI texts of the language, English filling the gaps."""
    english = cast(dict[str, str], catalogue(Language.EN)["ui"])
    return {**english, **cast(dict[str, str], catalogue(language).get("ui", {}))}


def web_catalogue(language: Language) -> Catalogue:
    """What the web page needs: UI texts and the translated names, English filling the gaps."""
    data = catalogue(language)
    terms = cast(dict[str, Any], catalogue(Language.EN)["terms"])
    return {
        "ui": ui_texts(language),
        "rooms": data.get("rooms", {}),
        "objects": data.get("objects", {}),
        "terms": {**terms, **cast(dict[str, Any], data.get("terms", {}))},
    }
