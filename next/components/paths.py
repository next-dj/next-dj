"""The template file a component call renders from, resolved and anchored for keys."""

from __future__ import annotations

import functools
from pathlib import Path

from next.conf import next_framework_settings
from next.utils import app_holding, on_forget_resolved_trees, resolve_base_dir


# Distinct ``current_template_path`` strings memoised, matching the other path caches.
_PATH_MEMO_MAX_ENTRIES = 2048


@functools.lru_cache(maxsize=_PATH_MEMO_MAX_ENTRIES)
def resolve_template_path(raw: str) -> Path:
    """Return the resolved file for a raw ``current_template_path`` string.

    Memoised per raw string and dropped along with the shared page-tree resolutions.
    """
    return Path(raw).resolve()


on_forget_resolved_trees(resolve_template_path.cache_clear)


def _setting_roots() -> tuple[Path, ...]:
    """Return the `DIRS` roots of the page and component backends, resolved."""
    roots: list[Path] = []
    for entry in (
        *next_framework_settings.PAGE_BACKENDS,
        *next_framework_settings.COMPONENT_BACKENDS,
    ):
        dirs = entry.get("DIRS") if isinstance(entry, dict) else None
        if isinstance(dirs, list | tuple):
            roots.extend(
                Path(raw).resolve() for raw in dirs if isinstance(raw, str | Path)
            )
    return tuple(roots)


@functools.lru_cache(maxsize=_PATH_MEMO_MAX_ENTRIES)
def anchored(name: str) -> str:
    """Return the template file `name` relative to the app or the root holding it.

    A key hashes it, so it stays the same across hosts and release directories.
    """
    path = Path(name)
    if not path.is_absolute():
        return name
    held = app_holding(path)
    if held is not None:
        return f"{held[0]}:{held[1]}"
    real = path.resolve()
    for position, root in enumerate(_setting_roots()):
        if real.is_relative_to(root):
            return f"{position}:{real.relative_to(root).as_posix()}"
    base = resolve_base_dir()
    if base is not None and real.is_relative_to(base.resolve()):
        return real.relative_to(base.resolve()).as_posix()
    return name


on_forget_resolved_trees(anchored.cache_clear)


__all__ = ["anchored", "resolve_template_path"]
