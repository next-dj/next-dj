"""Reads of the nested `NEXT_FRAMEWORK` scopes, with missing keys taken from `DEFAULTS`.

The merge replaces a default scope with the user mapping whole, so a key the user
leaves out is read from `DEFAULTS` here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, cast

from .defaults import DEFAULTS
from .frozen import freeze
from .settings import next_framework_settings


# Frozen once, so a key the user omits is read without a copy per call.
_FROZEN_DEFAULTS: Final = cast("Mapping[str, Any]", freeze(DEFAULTS))


def scope_value(name: str, key: str) -> object:
    """Return `key` of the `name` scope, or its default when the user omits it."""
    held = getattr(next_framework_settings, name)
    if isinstance(held, Mapping) and key in held:
        return held[key]
    return _FROZEN_DEFAULTS[name].get(key)


__all__ = ["scope_value"]
