"""Helpers for writing compact `NEXT_FRAMEWORK` settings blocks."""

from __future__ import annotations

import copy
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from .defaults import DEFAULTS


_BACKEND_LIST_KEYS = frozenset(
    {
        "DEFAULT_PAGE_BACKENDS",
        "DEFAULT_COMPONENT_BACKENDS",
        "DEFAULT_STATIC_BACKENDS",
    }
)


def extend_default_backend(
    key: str,
    *,
    index: int = 0,
    **overrides: Any,  # noqa: ANN401
) -> list[dict[str, Any]]:
    """Return a `NEXT_FRAMEWORK[key]` list with one backend entry patched.

    The returned list is a deep copy of the default entries with the
    entry at `index` updated by `overrides`. Nested dicts such as
    `OPTIONS` are merged instead of replaced so partial overrides do not
    drop adjacent keys.

    Raises `ImproperlyConfigured` when `key` is not a known backend-list
    setting. Raises `IndexError` when `index` is out of range for the
    default list.
    """
    if key not in _BACKEND_LIST_KEYS:
        allowed = ", ".join(sorted(_BACKEND_LIST_KEYS))
        msg = f"Unknown backend list {key!r}. Allowed keys are {allowed}."
        raise ImproperlyConfigured(msg)
    entries: list[dict[str, Any]] = copy.deepcopy(DEFAULTS[key])
    if index < 0 or index >= len(entries):
        msg = f"Backend index {index} is out of range for {key} (size {len(entries)})."
        raise IndexError(msg)
    entry = entries[index]
    for override_key, override_value in overrides.items():
        current = entry.get(override_key)
        if isinstance(current, dict) and isinstance(override_value, dict):
            merged = dict(current)
            merged.update(override_value)
            entry[override_key] = merged
        else:
            entry[override_key] = override_value
    return entries


__all__ = ["extend_default_backend"]
