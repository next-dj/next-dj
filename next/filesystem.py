"""Filesystem path helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.conf import settings


def resolve_base_dir() -> Path | None:
    """Return ``settings.BASE_DIR`` as a :class:`~pathlib.Path`, or ``None``."""
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
    """Split ``DIRS`` into existing directory roots and segment names (file router)."""
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
