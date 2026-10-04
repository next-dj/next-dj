"""Per-`page.py` metadata-callable registry and the memo of the folded chains."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from next.caches import BoundedCache
from next.introspect import MisattributedContext, MisattributionLog, callable_name
from next.pages.errors import PageMetadataConflictError
from next.pages.signals import metadata_registered


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .chain import ChainEntry


class PageMetadataEntry(NamedTuple):
    """One metadata callable registered for a `page.py` file."""

    func: Callable[..., Any]
    inherit: bool


class MetadataRegistrations(NamedTuple):
    """The registered callable names and misattributions, read together for checks."""

    names: dict[Path, tuple[str, ...]]
    misattributed: tuple[MisattributedContext, ...]


def _one_run(first: Callable[..., Any], second: Callable[..., Any]) -> bool:
    """Whether two distinct callables were defined by one run of the same module.

    A re-executed `page.py` runs in a fresh namespace, so its callable replaces the old.
    """
    held = getattr(first, "__globals__", None)
    return (
        first is not second
        and held is not None
        and held is getattr(second, "__globals__", None)
    )


class PageMetadataRegistry:
    """Register the metadata callable of each `page.py` and memoise the chains.

    A per-file stamp changes only when a registration changes the callable a chain runs.
    """

    def __init__(self) -> None:
        """Start with an empty registry and no memoised chain."""
        self._entries: dict[Path, PageMetadataEntry] = {}
        self._misattributions = MisattributionLog()
        self._version = 0
        self._stamps: dict[Path, int] = {}
        self._chains: BoundedCache[Path, ChainEntry] = BoundedCache()

    @property
    def version(self) -> int:
        """Monotonic counter bumped on every write."""
        return self._version

    def _bump(self) -> None:
        """Increment the version after a write."""
        self._version += 1

    def _stamp(self, file_path: Path) -> None:
        """Set the stamp of `file_path` above every stamp issued so far."""
        self._stamps[file_path] = self._version + 1

    def reset(self) -> None:
        """Drop every registration and memoised chain for a re-executed `page.py`."""
        self._entries.clear()
        self._misattributions.clear()
        self._stamps.clear()
        self._chains.clear()
        self._bump()

    def stamps(self, paths: tuple[Path, ...]) -> tuple[int | None, ...]:
        """Return the registration stamp of each path, `None` where none registered."""
        stamps = self._stamps
        return tuple(stamps.get(path) for path in paths)

    def chain(self, file_path: Path) -> ChainEntry | None:
        """Return the memoised chain of `file_path` without revalidating it."""
        return self._chains.get(file_path)

    def remember(self, file_path: Path, entry: ChainEntry) -> None:
        """Memoise the chain built for `file_path`, replacing an earlier one."""
        self._chains[file_path] = entry

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

    def registrations(self) -> MetadataRegistrations:
        """Return the names and the misattributions together."""
        return MetadataRegistrations(self.registered_names(), self.misattributed())

    def entry(self, file_path: Path) -> PageMetadataEntry | None:
        """Return the callable registered for `file_path`, if any."""
        return self._entries.get(file_path)

    def register(
        self, file_path: Path, func: Callable[..., Any], *, inherit: bool = False
    ) -> None:
        """Bind `func` to `file_path`, refusing a second callable from the same run.

        A re-executed module registers the same name again, which keeps the stamp.
        """
        existing = self._entries.get(file_path)
        if existing is not None and _one_run(existing.func, func):
            detail = (
                "two @page.metadata callables, "
                f"{callable_name(existing.func)!r} and {callable_name(func)!r}"
            )
            raise PageMetadataConflictError(file_path, detail)
        if (
            existing is None
            or callable_name(existing.func) != callable_name(func)
            or existing.inherit != inherit
        ):
            self._stamp(file_path)
        self._entries[file_path] = PageMetadataEntry(func=func, inherit=inherit)
        self._bump()
        if metadata_registered.has_listeners(PageMetadataRegistry):
            metadata_registered.send(
                sender=PageMetadataRegistry, file_path=file_path, inherit=inherit
            )


__all__ = ["MetadataRegistrations", "PageMetadataEntry", "PageMetadataRegistry"]
