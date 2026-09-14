"""Name a callable and the file it was declared in, for decorator registration.

Every area that registers a decorated object reads both, so the walk sits below.
"""

from __future__ import annotations

import functools
import inspect
from pathlib import Path
from types import CodeType
from typing import TYPE_CHECKING, Any, NamedTuple


if TYPE_CHECKING:
    from collections.abc import Callable


_CLASS_BODY_MEMBERS: tuple[str, ...] = ("__call__", "__init__")


def code_filename(func: object) -> str | None:
    """Return the source file behind ``func.__code__``, or ``None`` when it has none."""
    target = func
    if callable(func):
        try:
            target = inspect.unwrap(func)
        except ValueError:
            # A ``__wrapped__`` cycle names no innermost function, so the
            # outermost wrapper answers rather than the registration failing.
            target = func
    code = getattr(target, "__code__", None)
    return code.co_filename if isinstance(code, CodeType) else None


def _class_filename(cls: type) -> str | None:
    """Return the file declaring ``cls``, reading its own body when the module is gone.

    A `page.py` execs from a spec, so `sys.modules` names no file for its classes.
    """
    try:
        return inspect.getfile(cls)
    except (OSError, TypeError):
        for name in _CLASS_BODY_MEMBERS:
            filename = code_filename(cls.__dict__.get(name))
            if filename is not None:
                return filename
    return None


def defining_file(obj: object) -> Path:
    """Return the file where ``obj`` was declared, for decorator registration.

    A class built by ``type()`` in foreign code keeps no link to its caller's file,
    so a code object from the object's own body answers when ``sys.modules`` names
    none.
    """
    if isinstance(obj, functools.partial):
        return defining_file(obj.func)
    if inspect.isclass(obj):
        filename = _class_filename(obj)
    elif callable(obj):
        filename = code_filename(obj) or code_filename(type(obj).__call__)
    else:
        filename = None
    if filename is not None:
        return Path(filename)
    msg = (
        f"next.dj could not determine the file where {obj!r} was declared, "
        "so the registration has no page or component to belong to. Declare a "
        "function with 'def' in the file that uses it and decorate that."
    )
    raise TypeError(msg)


def callable_name(obj: object) -> str:
    """Return the name a registration reports for ``obj`` in diagnostics.

    A partial and a callable instance carry no ``__name__``, so the name of
    the wrapped function or of the class stands in for one.
    """
    if isinstance(obj, functools.partial):
        return callable_name(obj.func)
    name = getattr(obj, "__name__", None)
    return name if isinstance(name, str) else type(obj).__name__


def describe_callable(func: Callable[..., Any]) -> str:
    """Return a human-readable name and source path for `func` in diagnostics."""
    name = callable_name(func)
    filename = code_filename(func)
    return f'"{name}"' if filename is None else f'"{name}" ({filename})'


class MisattributedContext(NamedTuple):
    """One registration whose callable was declared outside the file running it.

    Both files are kept because a diagnostic has to name the file that
    expected the value and the one the registration landed on.
    """

    registered_from: Path
    declared_in: Path
    name: str


class MisattributionLog:
    """Collect registrations bound to a file other than the one running them."""

    def __init__(self) -> None:
        """Start with no recorded registration."""
        # Keyed by the whole record so a module executed more than once
        # reports one diagnostic rather than one per execution.
        self._records: dict[MisattributedContext, None] = {}

    def record(
        self, registered_from: Path, declared_in: Path, func: Callable[..., Any]
    ) -> None:
        """Note that `func` bound to `declared_in` while `registered_from` ran."""
        entry = MisattributedContext(
            registered_from=registered_from,
            declared_in=declared_in,
            name=callable_name(func),
        )
        self._records[entry] = None

    def entries(self) -> tuple[MisattributedContext, ...]:
        """Return every recorded registration, in the order first seen."""
        return tuple(self._records)

    def clear(self) -> None:
        """Drop every recorded registration."""
        self._records.clear()


__all__ = [
    "MisattributedContext",
    "MisattributionLog",
    "callable_name",
    "code_filename",
    "defining_file",
    "describe_callable",
]
