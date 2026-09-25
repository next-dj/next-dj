"""Per-`page.py` metadata-callable registry and the memo of the folded chains."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from next.caches import BoundedCache
from next.introspect import MisattributedContext, MisattributionLog, callable_name
from next.pages.signals import metadata_registered


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .chain import ChainEntry


class PageMetadataEntry(NamedTuple):
    """One metadata callable registered for a `page.py` file."""

    func: Callable[..., Any]
    inherit: bool


class PageMetadataRegistry:
    """Register the metadata callable of each `page.py` and memoise the chains."""

    def __init__(self) -> None:
        """Start with an empty registry and no memoised chain."""
        self._entries: dict[Path, PageMetadataEntry] = {}
        self._conflicts: dict[Path, list[str]] = {}
        self._misattributions = MisattributionLog()
        self._version = 0
        self._chains: BoundedCache[Path, ChainEntry] = BoundedCache()

    @property
    def version(self) -> int:
        """Monotonic counter bumped on every write, which the chain memo keys off."""
        return self._version

    def _bump(self) -> None:
        """Mark the registry as moved so every memoised chain rebuilds."""
        self._version += 1

    def reset(self) -> None:
        """Drop every registration so a re-executed `page.py` repopulates it."""
        self._entries.clear()
        self._conflicts.clear()
        self._misattributions.clear()
        self._bump()

    def forget_chains(self) -> None:
        """Drop every memoised chain."""
        self._chains.clear()

    def misattributed(self) -> tuple[MisattributedContext, ...]:
        """Return every registration bound to a file other than the one running it."""
        return self._misattributions.entries()

    def note_misattribution(
        self, registered_from: Path, declared_in: Path, func: Callable[..., Any]
    ) -> None:
        """Record a callable declared outside the `page.py` that decorated it."""
        self._misattributions.record(registered_from, declared_in, func)

    def registered_names(self) -> dict[Path, tuple[str, ...]]:
        """Return the callable name registered per file, for the diagnostics."""
        return {
            file_path: (callable_name(entry.func),)
            for file_path, entry in self._entries.items()
        }

    def conflicts(self) -> dict[Path, tuple[str, ...]]:
        """Return the differently named callables that overwrote one another."""
        return {file_path: tuple(names) for file_path, names in self._conflicts.items()}

    def entry(self, file_path: Path) -> PageMetadataEntry | None:
        """Return the callable registered for `file_path`, if any."""
        return self._entries.get(file_path)

    def register(
        self, file_path: Path, func: Callable[..., Any], *, inherit: bool = False
    ) -> None:
        """Bind `func` to `file_path`, the last registration winning.

        A re-executed module registers the same name again, which is no conflict.
        """
        existing = self._entries.get(file_path)
        if existing is not None:
            existing_name = callable_name(existing.func)
            new_name = callable_name(func)
            if existing_name != new_name:
                self._conflicts.setdefault(file_path, [existing_name]).append(new_name)
        self._entries[file_path] = PageMetadataEntry(func=func, inherit=inherit)
        self._bump()
        metadata_registered.send(
            sender=PageMetadataRegistry, file_path=file_path, inherit=inherit
        )


__all__ = ["PageMetadataEntry", "PageMetadataRegistry"]
