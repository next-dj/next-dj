"""Registry of the `@sitemap.items` callables, keyed by page tree and trail."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from next.introspect import callable_name

from .signals import sitemap_items_registered


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class SitemapItemsEntry(NamedTuple):
    """One callable registered for a trail by the file running `@sitemap.items`."""

    file: Path
    trail: str
    func: Callable[..., Any]


class SitemapItemsRegistry:
    """Hold the items callables in registration order, the last one per key winning."""

    def __init__(self) -> None:
        """Start empty."""
        self._entries: list[SitemapItemsEntry] = []
        self._index: dict[tuple[Path, str], SitemapItemsEntry] = {}
        self._version = 0

    @property
    def version(self) -> int:
        """Monotonic counter bumped on every write."""
        return self._version

    def register(self, file: Path, trail: str, func: Callable[..., Any]) -> None:
        """Bind `func` to `trail` in the tree of `file`, replacing an earlier binding.

        A re-executed `sitemap.py` registers the same trail again, which is a replace.
        """
        entry = SitemapItemsEntry(file=file, trail=trail, func=func)
        key = (file, trail)
        existing = self._index.get(key)
        if existing is None:
            self._entries.append(entry)
        else:
            self._entries[self._entries.index(existing)] = entry
        self._index[key] = entry
        self._version += 1
        sitemap_items_registered.send(
            sender=SitemapItemsRegistry, file=file, trail=trail, func=func
        )

    def registered_names(self) -> dict[Path, tuple[str, ...]]:
        """Return the callable names registered per registering file, for the checks."""
        names: dict[Path, list[str]] = {}
        for entry in self._entries:
            names.setdefault(entry.file, []).append(callable_name(entry.func))
        return {file: tuple(found) for file, found in names.items()}

    def entries_for(self, file: Path) -> tuple[tuple[str, Callable[..., Any]], ...]:
        """Return `(trail, func)` for every callable the module at `file` registered."""
        return tuple(
            (entry.trail, entry.func) for entry in self._entries if entry.file == file
        )

    def reset(self) -> None:
        """Drop every registration so a re-executed `sitemap.py` repopulates it."""
        self._entries.clear()
        self._index.clear()
        self._version += 1


sitemap_items_registry = SitemapItemsRegistry()


__all__ = ["SitemapItemsEntry", "SitemapItemsRegistry", "sitemap_items_registry"]
