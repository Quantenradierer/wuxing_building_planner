"""Strategies by name. Rules refer to them by name or by import path `package.module:Class`."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from roomplanner.errors import RulesError

_REGISTRY: dict[str, dict[str, Callable[[], Any]]] = {}


def register[T](stage: str, name: str) -> Callable[[type[T]], type[T]]:
    def decorator(cls: type[T]) -> type[T]:
        _REGISTRY.setdefault(stage, {})[name] = cls
        return cls

    return decorator


def resolve(stage: str, name: str) -> Any:
    """Instantiate the strategy registered as `name`, or import it from `module:Class`."""
    if ":" in name:
        module_name, _, attribute = name.partition(":")
        try:
            return getattr(importlib.import_module(module_name), attribute)()
        except (ImportError, AttributeError) as error:
            raise RulesError(f"cannot load {stage} strategy {name!r}: {error}") from error
    try:
        return _REGISTRY[stage][name]()
    except KeyError:
        known = ", ".join(sorted(_REGISTRY.get(stage, {})))
        raise RulesError(f"unknown {stage} strategy {name!r} (known: {known})") from None
