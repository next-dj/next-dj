"""Watch-spec helpers for the development file reloader.

Built-in `(path, glob)` defaults come from `NEXT_FRAMEWORK`, and a registry
takes the extra pairs third-party apps contribute.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.backends import backend_entries
from next.components import component_extra_roots_from_config
from next.pages.watch import (
    get_pages_directories_for_watch,
    iter_pages_roots_with_components_folder_names,
)

from .signals import watch_specs_ready


if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


_registered_extra_watch_specs: list[tuple[Path, str]] = []


def register_autoreload_watch_spec(path: Path, glob: str) -> None:
    """Register one extra directory and glob pair for the file watcher.

    Built-in globs already come from `NEXT_FRAMEWORK`. Call this from
    your own `AppConfig.ready` if you need more trees watched without
    changing the `next` package.
    """
    _registered_extra_watch_specs.append((path, glob))


def _dedupe_watch_specs(specs: Iterable[tuple[Path, str]]) -> list[tuple[Path, str]]:
    """Drop duplicate `(path, glob)` pairs keyed on resolved path."""
    seen: set[tuple[Path, str]] = set()
    out: list[tuple[Path, str]] = []
    for path, glob in specs:
        try:
            key = (path.resolve(), glob)
        except OSError:
            key = (path, glob)
        if key not in seen:
            seen.add(key)
            out.append((path, glob))
    return out


def _iter_default_autoreload_watch_specs() -> list[tuple[Path, str]]:
    """Return the default watch specs for pages and filesystem components.

    `.djx` is intentionally omitted. Template edits do not restart the
    process, but Python entrypoints (`page.py`, `component.py`) trigger
    reload when their mtimes change.
    """
    specs: list[tuple[Path, str]] = [
        (p, "**/page.py") for p in get_pages_directories_for_watch()
    ]
    specs.extend(
        (root, f"**/{comp_name}/**/component.py")
        for root, comp_name in iter_pages_roots_with_components_folder_names()
    )
    for config in backend_entries("COMPONENT_BACKENDS"):
        specs.extend(
            (root, "**/component.py")
            for root in component_extra_roots_from_config(config)
        )
    return specs


def iter_all_autoreload_watch_specs() -> list[tuple[Path, str]]:
    """Return default watch specs plus registered extras, deduplicated."""
    specs = _dedupe_watch_specs(
        (*_iter_default_autoreload_watch_specs(), *_registered_extra_watch_specs)
    )
    watch_specs_ready.send(sender=iter_all_autoreload_watch_specs, specs=specs)
    return specs
