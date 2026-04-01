"""Filesystem path helpers."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from django.conf import settings


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


def caller_source_path(  # noqa: C901, PLR0912
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

    frame = inspect.currentframe()
    err_plain = "Could not determine caller file path"
    err_components = f"{err_plain}: no __file__ in caller frames"

    for _ in range(back_count):
        if not frame or not frame.f_back:
            raise RuntimeError(err_plain)
        frame = frame.f_back

    if skip_while_filename_endswith is not None:
        suffixes = skip_while_filename_endswith
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
        raise RuntimeError(err_plain)

    base, parent = skip_framework_file  # type: ignore[misc]
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
