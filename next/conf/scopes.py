"""The one read of a nested `NEXT_FRAMEWORK` scope, its gaps filled from `DEFAULTS`.

A user mapping replaces its default whole, so a key it leaves out reads the default.
"""

from __future__ import annotations

from collections.abc import Mapping

from .defaults import DEFAULTS
from .frozen import freeze
from .settings import next_framework_settings


def settings_scope(name: str) -> Mapping[str, object]:
    """Return the merged `name` scope, empty where the setting holds no mapping."""
    raw = getattr(next_framework_settings, name)
    return raw if isinstance(raw, Mapping) else {}


def scope_value(name: str, key: str) -> object:
    """Return `key` of the `name` scope, the default where the user leaves it out."""
    held = settings_scope(name)
    if key in held:
        return held[key]
    return freeze(DEFAULTS[name].get(key))


__all__ = ["scope_value", "settings_scope"]
