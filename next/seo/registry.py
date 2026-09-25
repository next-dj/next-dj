"""Registry of the `@sitemap.items` callables, keyed by page tree and trail."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from .signals import sitemap_items_registered


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class SitemapItemsEntry(NamedTuple):
    """One callable registered for the trail of one page tree."""

    root: Path
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

    def register(self, root: Path, trail: str, func: Callable[..., Any]) -> None:
        """Bind `func` to `trail` under `root`, replacing an earlier binding.

        A re-executed `sitemap.py` registers the same trail again, which is a replace.
        """
        entry = SitemapItemsEntry(root=root, trail=trail, func=func)
        key = (root, trail)
        existing = self._index.get(key)
        if existing is None:
            self._entries.append(entry)
        else:
            self._entries[self._entries.index(existing)] = entry
        self._index[key] = entry
        self._version += 1
        sitemap_items_registered.send(
            sender=SitemapItemsRegistry, root=root, trail=trail, func=func
        )

    def entries_for(self, root: Path) -> tuple[tuple[str, Callable[..., Any]], ...]:
        """Return `(trail, func)` for every callable registered under `root`."""
        return tuple(
            (entry.trail, entry.func) for entry in self._entries if entry.root == root
        )

    def reset(self, **kwargs) -> None:
        """Drop every registration so a re-executed `sitemap.py` repopulates it."""
        self._entries.clear()
        self._index.clear()
        self._version += 1


sitemap_items_registry = SitemapItemsRegistry()


__all__ = ["SitemapItemsEntry", "SitemapItemsRegistry", "sitemap_items_registry"]
