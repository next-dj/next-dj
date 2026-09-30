"""The dotted-path imports every settings key that names a class or a callable uses.

`import_class_cached` imports a backend once per process until a settings reload.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any, cast

from django.utils.module_loading import import_string


if TYPE_CHECKING:
    from collections.abc import Callable


@functools.cache
def import_class_cached(dotted_path: str) -> type[Any]:
    """Import a class by dotted path and memoise it until the memo is cleared."""
    return cast("type[Any]", import_string(dotted_path))


def import_callable(dotted: str) -> Callable[..., object] | None:
    """Return the callable a dotted path names, `None` when it names none."""
    try:
        target = import_string(dotted)
    except ImportError:
        return None
    return target if callable(target) else None


# Named for what a caller drops rather than for the memo behind it.
clear_import_cache = import_class_cached.cache_clear
