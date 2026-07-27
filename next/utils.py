"""Filesystem path helpers."""

from __future__ import annotations

import functools
import inspect
from pathlib import Path
from types import CodeType
from typing import TYPE_CHECKING, Any

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


def _code_filename(func: Callable[..., Any]) -> str | None:
    """Return the source file behind ``func.__code__``, or ``None`` when it has none."""
    code = getattr(inspect.unwrap(func), "__code__", None)
    return code.co_filename if isinstance(code, CodeType) else None


def defining_file(obj: object) -> Path:
    """Return the file where ``obj`` was declared, for decorator registration.

    Wrappers stay transparent only while they set ``__wrapped__`` through
    :func:`functools.wraps`, and a class built by ``type()`` inside foreign
    code keeps no link to the file that asked for it. A callable instance
    answers through the code object of its ``__call__``, which names the
    file even for a module absent from ``sys.modules``.
    """
    if inspect.isclass(obj):
        return Path(inspect.getfile(obj))
    if isinstance(obj, functools.partial):
        return defining_file(obj.func)
    if callable(obj):
        filename = _code_filename(obj) or _code_filename(type(obj).__call__)
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
