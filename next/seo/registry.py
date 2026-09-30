"""Registry of the `@sitemap.items` callables, keyed by registering file and trail."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, NamedTuple

from next.introspect import callable_name

from .signals import sitemap_items_registered


if TYPE_CHECKING:
    from pathlib import Path


type KwargsOf = Callable[[Any], Mapping[str, object]]


class SitemapItemsEntry(NamedTuple):
    """One callable registered for a trail by the file running `@sitemap.items`.

    `kwargs` and `lastmod` read the URL kwargs and the date off each row it lists.
    """

    file: Path
    trail: str
    func: Callable[..., Any]
    section: str | None = None
    kwargs: KwargsOf | None = None
    lastmod: str | None = None


class SitemapItemsConflict(NamedTuple):
    """Two callables one file registered for the same trail, the later one listing."""

    file: Path
    trail: str
    replaced: str
    kept: str


class SitemapItemsRegistry:
    """Hold the items callables in registration order, the last one per key winning.

    A second callable on one trail of one file is a conflict the checks report.
    """

    def __init__(self) -> None:
        """Start empty."""
        self._entries: list[SitemapItemsEntry] = []
        self._index: dict[tuple[Path, str], SitemapItemsEntry] = {}
        self._conflicts: list[SitemapItemsConflict] = []
        self._version = 0

    @property
    def version(self) -> int:
        """Monotonic counter bumped on every write."""
        return self._version

    def register(self, entry: SitemapItemsEntry) -> None:
        """Bind the callable of `entry` to its trail, replacing an earlier binding.

        A re-executed `sitemap.py` forgets its file first, so a repeat is a conflict.
        """
        key = (entry.file, entry.trail)
        existing = self._index.get(key)
        if existing is None:
            self._entries.append(entry)
        else:
            self._entries[self._entries.index(existing)] = entry
            if existing.func is not entry.func:
                self._conflicts.append(
                    SitemapItemsConflict(
                        entry.file,
                        entry.trail,
                        callable_name(existing.func),
                        callable_name(entry.func),
                    )
                )
        self._index[key] = entry
        self._version += 1
        sitemap_items_registered.send(
            sender=SitemapItemsRegistry,
            file=entry.file,
            trail=entry.trail,
            func=entry.func,
        )

    def forget(self, file: Path) -> None:
        """Drop what `file` registered, so executing it again starts from nothing."""
        self._conflicts = [item for item in self._conflicts if item.file != file]
        kept = [entry for entry in self._entries if entry.file != file]
        if len(kept) == len(self._entries):
            return
        self._entries = kept
        self._index = {(entry.file, entry.trail): entry for entry in kept}
        self._version += 1

    def registered_names(self) -> dict[Path, tuple[str, ...]]:
        """Return the callable names registered per registering file, for the checks."""
        names: dict[Path, list[str]] = {}
        for entry in self._entries:
            names.setdefault(entry.file, []).append(callable_name(entry.func))
        return {file: tuple(found) for file, found in names.items()}

    def conflicts(self) -> tuple[SitemapItemsConflict, ...]:
        """Return every trail a file bound twice, for the checks."""
        return tuple(self._conflicts)

    def entries_for(self, file: Path) -> tuple[SitemapItemsEntry, ...]:
        """Return every entry the module at `file` registered, in order."""
        return tuple(entry for entry in self._entries if entry.file == file)

    def reset(self) -> None:
        """Drop every registration so a re-executed `sitemap.py` repopulates it."""
        self._entries.clear()
        self._index.clear()
        self._conflicts.clear()
        self._version += 1


sitemap_items_registry = SitemapItemsRegistry()


__all__ = [
    "KwargsOf",
    "SitemapItemsConflict",
    "SitemapItemsEntry",
    "SitemapItemsRegistry",
    "sitemap_items_registry",
]
