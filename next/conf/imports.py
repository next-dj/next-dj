"""Import helper backed by a module-level dotted-path cache.

`import_class_cached` memoises lookups so a backend named by a settings key imports once
per process, and `NextFrameworkSettings.reload` clears the cache.
"""

from __future__ import annotations

from typing import Any

from django.utils.module_loading import import_string


_import_class_cache: dict[str, type[Any]] = {}


def import_class_cached(dotted_path: str) -> type[Any]:
    """Import a class by dotted path and cache it until the cache is cleared."""
    if dotted_path not in _import_class_cache:
        _import_class_cache[dotted_path] = import_string(dotted_path)
    return _import_class_cache[dotted_path]


def clear_import_cache() -> None:
    """Drop every cached dotted-path import."""
    _import_class_cache.clear()
