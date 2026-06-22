"""Filesystem path helpers."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.conf import settings


if TYPE_CHECKING:
    from types import FrameType


def resolve_base_dir() -> Path | None:
    """Return ``settings.BASE_DIR`` as a ``pathlib.Path``, or ``None``."""
    raw = getattr(settings, "BASE_DIR", None)
    if isinstance(raw, Path):
        return raw
    if isinstance(raw, str):
        return Path(raw)
    return None


def _classify_one_dir_entry(
    item: Path,
    base_dir: Path | None,
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
    entries: list[Any] | tuple[Any, ...] | None,
    base_dir: Path | None,
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


_CALLER_PATH_ERROR = "Could not determine caller file path"


def caller_source_path(
    *,
    back_count: int = 1,
    max_walk: int = 15,
    skip_while_filename_endswith: tuple[str, ...] | None = None,
    skip_framework_file: tuple[str, str] | None = None,
) -> Path:
    """Resolve ``Path`` of the caller module's ``__file__`` for decorator registration.

    ``back_count`` is how many frames to step up before scanning. Use this to skip
    past a decorator wrapper.

    For pages and forms pass ``skip_while_filename_endswith``, for example
    ``("pages.py",)``. Walk frames until ``__file__`` is missing or no longer ends
    with one of those suffixes. Then return that path.

    For components pass ``skip_framework_file`` as ``(basename, parent_dir_name)``,
    for example ``("components.py", "next")``. Only ``str`` paths ending in ``.py``
    are considered. The resolved framework module path is skipped.
    """
    if skip_while_filename_endswith is not None and skip_framework_file is not None:
        msg = "Specify only one of skip_while_filename_endswith or skip_framework_file"
        raise ValueError(msg)
    if skip_while_filename_endswith is None and skip_framework_file is None:
        msg = "Specify skip_while_filename_endswith or skip_framework_file"
        raise ValueError(msg)

    frame = _walk_back(inspect.currentframe(), back_count)
    if skip_while_filename_endswith is not None:
        return _scan_by_suffix(frame, skip_while_filename_endswith, max_walk)
    if skip_framework_file is None:  # pragma: no cover
        raise RuntimeError(_CALLER_PATH_ERROR)
    return _scan_framework_file(frame, skip_framework_file, max_walk)


def _walk_back(frame: FrameType | None, back_count: int) -> FrameType | None:
    """Step up ``back_count`` frames before scanning, raising when they run out."""
    for _ in range(back_count):
        if not frame or not frame.f_back:
            raise RuntimeError(_CALLER_PATH_ERROR)
        frame = frame.f_back
    return frame


def _scan_by_suffix(
    frame: FrameType | None,
    suffixes: tuple[str, ...],
    max_walk: int,
) -> Path:
    """Return the first caller frame whose ``__file__`` is not a framework suffix."""
    for _ in range(max_walk):
        if not frame:
            break
        raw = frame.f_globals.get("__file__")
        if raw and isinstance(raw, str):
            if any(raw.endswith(sfx) for sfx in suffixes):
                frame = frame.f_back
                continue
            return Path(raw)
        frame = frame.f_back
    raise RuntimeError(_CALLER_PATH_ERROR)


def _scan_framework_file(
    frame: FrameType | None,
    skip_framework_file: tuple[str, str],
    max_walk: int,
) -> Path:
    """Return the first caller frame outside the named framework module file."""
    base, parent = skip_framework_file
    err_components = f"{_CALLER_PATH_ERROR}: no __file__ in caller frames"
    for _ in range(max_walk):
        if not frame:
            break
        raw = frame.f_globals.get("__file__")
        if isinstance(raw, str) and raw.endswith(".py"):
            path = Path(raw).resolve()
            if path.name == base and path.parent.name == parent:
                frame = frame.f_back
                continue
            return path
        frame = frame.f_back
    raise RuntimeError(err_components)
