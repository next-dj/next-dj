"""Reads of the nested `NEXT_FRAMEWORK` scopes, with missing keys taken from `DEFAULTS`.

The merge replaces a default scope with the user mapping whole, so a key the user
leaves out is read from `DEFAULTS` here.
"""

from __future__ import annotations

from collections.abc import Mapping

from .defaults import DEFAULTS
from .frozen import freeze
from .settings import next_framework_settings


def settings_scope(name: str) -> Mapping[str, object]:
    """Return the merged `name` scope, or an empty mapping when it holds no mapping."""
    raw = getattr(next_framework_settings, name)
    return raw if isinstance(raw, Mapping) else {}


def scope_value(name: str, key: str) -> object:
    """Return `key` of the `name` scope, or its default when the user omits it."""
    held = settings_scope(name)
    if key in held:
        return held[key]
    return freeze(DEFAULTS[name].get(key))


__all__ = ["scope_value", "settings_scope"]
