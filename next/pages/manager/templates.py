"""The per-page template layers and the source mtimes that date them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.caches import BoundedCache
from next.pages.loaders import build_registered_loaders
from next.utils import stat_mtime_ns, template_edits_watched


if TYPE_CHECKING:
    from pathlib import Path

    from django.template import Template

    from next.pages.loaders import LayoutTemplateLoader


# Mtimes of every source behind one composition, keyed by its page path.
type SourceMtimes = BoundedCache[Path, dict[Path, int]]


class PageTemplateCache:
    """Hold the composed source of a page, its compiled form, and its layout skeleton.

    Each layer carries its own bound, so unbounded page paths evict rather than grow.
    """

    def __init__(self, layout_loader: LayoutTemplateLoader) -> None:
        """Start empty, composing through `layout_loader` when a layer is rebuilt."""
        self.composed: BoundedCache[Path, str] = BoundedCache()
        self.compiled: BoundedCache[Path, Template] = BoundedCache()
        self.skeleton: BoundedCache[Path, str] = BoundedCache()
        # The skeleton keeps its own snapshot, because the composed layer refreshes
        # its own on eviction and would otherwise hide a layout edit.
        self.composed_sources: SourceMtimes = BoundedCache()
        self.skeleton_sources: SourceMtimes = BoundedCache()
        self._layout_loader = layout_loader

    def clear(self) -> None:
        """Drop every layer and the snapshots behind them."""
        self.composed.clear()
        self.compiled.clear()
        self.skeleton.clear()
        self.composed_sources.clear()
        self.skeleton_sources.clear()

    def forget_composed(self, file_path: Path) -> None:
        """Drop the composed source of one page and the snapshot behind it."""
        self.composed.pop(file_path)
        self.composed_sources.pop(file_path)

    def record_composed(self, file_path: Path) -> None:
        """Snapshot the sources the composed layer of `file_path` was built from."""
        self._record(file_path, self.composed_sources)

    def record_skeleton(self, file_path: Path) -> None:
        """Snapshot the sources the layout skeleton of `file_path` was built from."""
        self.skeleton_sources.pop(file_path)
        self._record(file_path, self.skeleton_sources)

    def composed_is_stale(self, file_path: Path) -> bool:
        """Whether a source behind the composed layer of `file_path` moved."""
        return self._is_stale(file_path, self.composed_sources)

    def skeleton_is_stale(self, file_path: Path) -> bool:
        """Whether a source behind the layout skeleton of `file_path` moved."""
        return self._is_stale(file_path, self.skeleton_sources)

    def source_paths(self, file_path: Path) -> list[Path]:
        """Return every path whose change alters the composition of this page.

        The directories come along with the files, because a `layout.djx`
        created or deleted there moves no mtime a file-only snapshot sees.
        """
        paths: list[Path] = []
        for loader in build_registered_loaders():
            source = loader.source_path(file_path)
            if source is not None:
                paths.append(source)
        layout_files, watched_dirs = self._layout_loader.layout_sources(file_path)
        paths += layout_files
        paths += watched_dirs
        return paths

    def _record(self, file_path: Path, store: SourceMtimes) -> None:
        """Snapshot mtimes of the template sources of `file_path` into `store`.

        Taken whether or not the process watches edits, because an entry
        composed with the watch off would hold nothing to compare against.
        """
        paths = self.source_paths(file_path)
        if not paths:
            return
        mtimes: dict[Path, int] = {}
        for source in paths:
            mtime = stat_mtime_ns(source)
            if mtime is not None:
                mtimes[source] = mtime
        if mtimes:
            # Bounded with the layer it shadows, so an evicted page leaves no
            # snapshot of sources nothing composes from any more.
            store[file_path] = mtimes

    def _is_stale(self, file_path: Path, store: SourceMtimes) -> bool:
        """Return whether any source tracked in `store` changed on disk.

        Compared for inequality rather than growth, so a checkout moving mtime
        backwards still reads as changed, same as a source that no longer stats.
        """
        if not template_edits_watched():
            return False
        stored = store.get(file_path)
        if not stored:
            return False
        return any(stat_mtime_ns(p) != old_mtime for p, old_mtime in stored.items())


__all__ = ["PageTemplateCache", "SourceMtimes"]
