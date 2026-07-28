"""Filesystem path helpers and the declaration-site attribution they back."""

from __future__ import annotations

import functools
import inspect
from pathlib import Path
from types import CodeType
from typing import TYPE_CHECKING, Any, NamedTuple

from django.conf import settings


if TYPE_CHECKING:
    from collections.abc import Callable


def resolve_base_dir() -> Path | None:
    """Return ``settings.BASE_DIR`` as a ``pathlib.Path``, or ``None``."""
    raw = getattr(settings, "BASE_DIR", None)
    if isinstance(raw, Path):
        return raw
    if isinstance(raw, str):
        return Path(raw)
    return None


def _classify_one_dir_entry(
    item: Path, base_dir: Path | None
) -> tuple[str, Path | str | None]:
    if item.is_absolute():
        if item.exists() and item.is_dir():
            return "path", item
        return "segment", item.name

    s = str(item).replace("\\", "/")
    if "/" in s:
        if base_dir is not None:
            cand = (base_dir / item).resolve()
            if cand.exists() and cand.is_dir():
                return "path", cand
        return "segment", Path(s).name or None

    if base_dir is not None:
        cand = base_dir / item
        if cand.exists() and cand.is_dir():
            return "path", cand.resolve()

    return "segment", item.name


def classify_dirs_entries(
    entries: list[Any] | tuple[Any, ...] | None, base_dir: Path | None
) -> tuple[list[Path], frozenset[str]]:
    """Split ``DIRS`` into directory roots and URL segment names (file router)."""
    path_roots: list[Path] = []
    segments: set[str] = set()
    if not entries:
        return path_roots, frozenset()

    for raw in entries:
        if raw is None:
            continue
        item = Path(raw) if not isinstance(raw, Path) else raw
        s = str(item)
        if not s or s == ".":
            continue

        kind, value = _classify_one_dir_entry(item, base_dir)
        if kind == "path" and isinstance(value, Path):
            path_roots.append(value.resolve())
        elif kind == "segment" and isinstance(value, str) and value:
            segments.add(value)

    return path_roots, frozenset(segments)


_CLASS_BODY_MEMBERS: tuple[str, ...] = ("__call__", "__init__")


def _code_filename(func: object) -> str | None:
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

    The file router execs a ``page.py`` from a spec it never registers, so
    ``sys.modules`` names no file for classes declared there. A method written
    in the class body still carries the path in its code object.
    """
    try:
        return inspect.getfile(cls)
    except (OSError, TypeError):
        for name in _CLASS_BODY_MEMBERS:
            filename = _code_filename(cls.__dict__.get(name))
            if filename is not None:
                return filename
    return None


def defining_file(obj: object) -> Path:
    """Return the file where ``obj`` was declared, for decorator registration.

    Wrappers stay transparent only while they set ``__wrapped__`` through
    :func:`functools.wraps`, and a class built by ``type()`` inside foreign
    code keeps no link to the file that asked for it. Where ``sys.modules``
    names no file, a code object from the object's own body answers instead.
    """
    if isinstance(obj, functools.partial):
        return defining_file(obj.func)
    if inspect.isclass(obj):
        filename = _class_filename(obj)
    elif callable(obj):
        filename = _code_filename(obj) or _code_filename(type(obj).__call__)
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
