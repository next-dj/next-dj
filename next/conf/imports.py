"""Import helpers backed by a module-level dotted-path cache.

`import_class_cached` memoises dotted-path lookups across reloads of the
framework settings. `perform_import` wraps the cache with the dotted-path
conversion rules used for future `IMPORT_STRINGS` entries. The cache is
cleared by `NextFrameworkSettings.reload`.
"""

from __future__ import annotations

from typing import Any

from django.utils.module_loading import import_string


IMPORT_STRINGS: frozenset[str] = frozenset()

_import_class_cache: dict[str, type[Any]] = {}


def import_class_cached(dotted_path: str) -> type[Any]:
    """Import a class by dotted path and cache it until the cache is cleared."""
    if dotted_path not in _import_class_cache:
        _import_class_cache[dotted_path] = import_string(dotted_path)
    return _import_class_cache[dotted_path]


def perform_import(val: Any, setting_name: str) -> Any:  # noqa: ANN401
    """Resolve a dotted import path through the cache when the value is a string."""
    if val is None or not isinstance(val, str):
        return val
    try:
        return import_class_cached(val)
    except ImportError as e:
        detail = f"{e!s}"
        msg = (
            f"Could not import {val!r} for Next framework setting "
            f"{setting_name!r}: {detail}"
        )
        raise ImportError(msg) from e


def clear_import_cache() -> None:
    """Drop every cached dotted-path import."""
    _import_class_cache.clear()
