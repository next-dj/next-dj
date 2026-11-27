"""Dotted-path imports for the settings keys that name a class or a callable.

`import_class_cached` imports a backend once per process until a settings reload.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, cast

from django.utils.module_loading import import_string

from next.caches import FRAMEWORK, memo


if TYPE_CHECKING:
    from collections.abc import Callable


@memo(FRAMEWORK)
def import_class_cached(dotted_path: str) -> type[Any]:
    """Import a class by dotted path and memoise it until the memo is cleared."""
    return cast("type[Any]", import_string(dotted_path))


def import_callable(dotted: str) -> Callable[..., object] | None:
    """Return the callable a dotted path names, `None` when it does not import one."""
    try:
        target = import_string(dotted)
    except ImportError:
        return None
    return target if callable(target) else None


def import_problem(error: ImportError) -> str:
    """Return how a report names a dotted path that does not import."""
    return f"does not import ({error})"


def import_target(dotted: str) -> object:
    """Import what a dotted path names, any import-time failure folded into ImportError.

    A raising module would otherwise abort a whole check run or the request it serves.
    """
    try:
        return import_string(dotted)
    except Exception as exc:
        msg = f"{dotted} raised {type(exc).__name__}: {exc}"
        raise ImportError(msg) from exc


def import_backend_class(dotted_path: str) -> type[Any]:
    """Import a dotted backend class once, any import-time failure an ImportError."""
    try:
        return import_class_cached(dotted_path)
    except Exception as exc:
        msg = f"{dotted_path} raised {type(exc).__name__}: {exc}"
        raise ImportError(msg) from exc


def class_fault(klass: object, base: type) -> str | None:
    """Return why `klass` is no concrete subclass of `base`, `None` when it is one."""
    if not isinstance(klass, type):
        return "names no class"
    if not issubclass(klass, base):
        return f"is no subclass of {base.__name__}"
    if inspect.isabstract(klass):
        return "is an abstract class"
    return None


def class_problem(dotted: str, base: type) -> str | None:
    """Return why `dotted` names no concrete subclass of `base`, `None` when it does."""
    try:
        klass: object = import_backend_class(dotted)
    except ImportError as exc:
        return import_problem(exc)
    return class_fault(klass, base)


def callable_problem(dotted: str) -> str | None:
    """Return why `dotted` names no callable, `None` when it does."""
    try:
        target = import_target(dotted)
    except ImportError as exc:
        return import_problem(exc)
    return None if callable(target) else "names no callable"


# Named for what a caller drops rather than for the memo behind it.
clear_import_cache = import_class_cached.cache_clear
