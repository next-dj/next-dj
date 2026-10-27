"""Reads of the nested `NEXT_FRAMEWORK` scopes, with missing keys taken from `DEFAULTS`.

The merge replaces a default scope whole, so a key the user leaves out is read here.
"""

from __future__ import annotations

from collections.abc import Mapping

from .merge import FROZEN_DEFAULTS
from .settings import next_framework_settings


def scope_value(name: str, key: str) -> object:
    """Return `key` of the `name` scope, or its default when the user omits it."""
    held = getattr(next_framework_settings, name)
    if isinstance(held, Mapping) and key in held:
        return held[key]
    return FROZEN_DEFAULTS[name].get(key)


__all__ = ["scope_value"]
