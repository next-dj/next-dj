"""Django staticfiles finder that exposes next-dj co-located assets.

The finder surfaces every `template.css`, `layout.js`, and `component.css` plus any
stems registered on the stem registry under the `next/` staticfiles namespace. The usual
`{% static "next/about.css" %}` call works without the user configuring anything.

The logical-path and source-file mapping is computed by the co-located asset discovery
helper below, which shares the same `PathResolver` used at request-time discovery. The
two layers agree on every URL.

Staticfiles asks per referenced asset, so the answer is held until something it was
read from moves. The freshness token is the one the asset plans use, a generation per
registry and a directory snapshot taken only where template edits are watched.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple, overload, override

from django.contrib.staticfiles.finders import BaseFinder
from django.contrib.staticfiles.utils import matches_patterns
from django.core.files import File
from django.core.files.storage import Storage

from next.components import component_watch_roots, get_component_paths_for_watch
from next.pages.registry import (
    get_layout_djx_paths_for_watch,
    get_template_djx_paths_for_watch,
)
from next.pages.watch import get_pages_directories_for_watch
from next.utils import stat_mtime_ns, template_edits_watched

from .assets import StaticNamespace, default_kinds
from .discovery import PathResolver, default_stems


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from .discovery import StemRegistry


def _collect_stem_static_files(
    out: dict[str, Path],
    directory: Path,
    logical_name: str,
    role: str,
    stems: StemRegistry,
) -> None:
    """Add `{stem}.<kind>` files found in the directory to the output map.

    Every registered kind is probed for each stem of the given role. Every
    caller hands in a resolved directory, so a file that is no symlink is
    already spelled the way request-time discovery spells it and only a
    symlinked one is worth walking a whole path back to its target.
    """
    kinds = default_kinds.kinds()
    for stem in stems.stems(role):
        for kind in kinds:
            suffix = default_kinds.extension(kind)
            candidate = directory / f"{stem}{suffix}"
            if not candidate.exists():  # pragma: no cover
                continue
            static_path = f"{StaticNamespace.NEXT}/{logical_name}{suffix}"
            source = candidate.resolve() if candidate.is_symlink() else candidate
            out.setdefault(static_path, source)


def discover_colocated_static_assets() -> dict[str, Path]:
    """Map staticfiles logical paths to absolute source files on disk.

    The helper scans every configured page-backend tree plus registered
    components. It honors the process-wide stem and kind registries, so
    custom stems registered during `AppConfig.ready` are picked up.
    """
    out: dict[str, Path] = {}
    # Resolved already, because that is what the watch layer promises.
    page_roots = tuple(get_pages_directories_for_watch())
    resolver = PathResolver(lambda: page_roots)

    for template_path in get_template_djx_paths_for_watch():
        page_root = resolver.find_page_root(template_path)
        if page_root is None:
            continue
        template_dir = template_path.parent.resolve()
        logical_name = resolver.logical_name_for_template(template_dir, page_root)
        _collect_stem_static_files(
            out, template_dir, logical_name, "template", default_stems
        )

    for layout_path in get_layout_djx_paths_for_watch():
        page_root = resolver.find_page_root(layout_path)
        if page_root is None:  # pragma: no cover
            continue
        layout_dir = layout_path.parent.resolve()
        logical_name = resolver.logical_name_for_layout(layout_dir, page_root)
        _collect_stem_static_files(
            out, layout_dir, logical_name, "layout", default_stems
        )

    seen_component_dirs: set[Path] = set()
    for component_source in get_component_paths_for_watch():  # pragma: no cover
        component_dir = component_source.parent.resolve()
        if component_dir in seen_component_dirs:
            continue
        seen_component_dirs.add(component_dir)
        logical_name = f"components/{component_dir.name}"
        _collect_stem_static_files(
            out, component_dir, logical_name, "component", default_stems
        )

    return out


class _MappedSourceStorage(Storage):
    """Storage wrapper that serves files from an explicit path mapping."""

    def __init__(self, mapping: dict[str, Path]) -> None:
        """Store the explicit logical-path to absolute-path mapping."""
        self._mapping = mapping

    def _resolve(self, name: str) -> Path:
        if name not in self._mapping:
            msg = f"Unknown static file: {name}"
            raise FileNotFoundError(msg)
        return self._mapping[name]

    @override
    def exists(self, name: str) -> bool:
        """Return True when the logical name has a mapping and the file exists."""
        try:
            return self._resolve(name).exists()
        except FileNotFoundError:
            return False

    @override
    def open(self, name: str, mode: str = "rb") -> File:
        """Open the file behind the logical name for reading."""
        path = self._resolve(name)
        return File(path.open(mode))

    @override
    def path(self, name: str) -> str:
        """Return the absolute filesystem path behind the logical name."""
        return str(self._resolve(name))


class _ScanRoots(NamedTuple):
    """The trees one scan reads, page trees first and component trees after.

    A reconfigured tree moves no mtime the snapshot below would notice, so the
    roots ride the scan and are compared whatever the process watches.
    """

    pages: tuple[Path, ...]
    components: tuple[Path, ...]


class _Scan(NamedTuple):
    """One discovery answer, together with what it was read from."""

    mapping: dict[str, Path]
    storage: _MappedSourceStorage
    roots: _ScanRoots
    registries: tuple[int, int]
    watched: bool
    directories: tuple[tuple[Path, int | None], ...]


def _registry_generation() -> tuple[int, int]:
    """Return the generation of every registry a scan reads while it is built.

    The placeholder registry is left out, because the finder names files rather
    than slots and no registration there moves which file a logical name means.
    """
    return (default_stems.version, default_kinds.version)


def _scan_roots() -> _ScanRoots:
    """Return the page trees and the component trees a scan reads."""
    return _ScanRoots(
        tuple(get_pages_directories_for_watch()), tuple(component_watch_roots())
    )


def _stat_directory(directory: Path) -> os.stat_result | None:
    """Return the stat of `directory`, or `None` when it does not stat."""
    try:
        return directory.stat()
    except OSError:
        return None


def _child_directories(directory: Path) -> list[Path]:
    """Return the directories held directly by `directory`.

    A symlinked one counts, because the component glob reads through it and a
    watch set narrower than what the scan reads would miss a file landing there.
    """
    try:
        with os.scandir(directory) as entries:
            return [Path(entry.path) for entry in entries if entry.is_dir()]
    except OSError:
        return []


def _scan_directories(roots: _ScanRoots) -> tuple[tuple[Path, int | None], ...]:
    """Snapshot the mtime of every directory the trees of `roots` hold.

    A directory that does not stat is recorded as `None`, which no real mtime equals,
    so a tree that appears later rebuilds the scan that walked past it.
    """
    out: list[tuple[Path, int | None]] = []
    seen: set[tuple[int, int]] = set()
    stack = [*roots.pages, *roots.components]
    while stack:
        directory = stack.pop()
        info = _stat_directory(directory)
        out.append((directory, None if info is None else info.st_mtime_ns))
        if info is None:
            continue
        key = (info.st_dev, info.st_ino)
        if key in seen:
            continue
        seen.add(key)
        stack.extend(_child_directories(directory))
    return tuple(out)


def _build_scan() -> _Scan:
    """Discover every co-located asset and note what the answer was read from.

    The generations and the snapshot are taken before the walk, so a
    registration or a file landing while it runs leaves the scan stale rather
    than stamped as up to date. A process watching no template edit snapshots
    nothing, because there only a reconfiguration moves what the walk finds.
    """
    registries = _registry_generation()
    roots = _scan_roots()
    watched = template_edits_watched()
    directories = _scan_directories(roots) if watched else ()
    mapping = discover_colocated_static_assets()
    return _Scan(
        mapping, _MappedSourceStorage(mapping), roots, registries, watched, directories
    )


def _scan_stale(scan: _Scan) -> bool:
    """Whether anything the scan was read from has moved since.

    A registration moves no file and a reconfigured tree no mtime, so the generations
    and the roots are compared whatever the process watches, and only a process
    watching template edits pays the stats. A scan taken while nothing was watched
    snapshotted no directory, so it reads as stale the moment watching starts.
    """
    if scan.registries != _registry_generation():
        return True
    if scan.roots != _scan_roots():
        return True
    watched = template_edits_watched()
    if watched != scan.watched:
        return True
    if not watched:
        return False
    return any(
        stat_mtime_ns(directory) != mtime for directory, mtime in scan.directories
    )


class NextStaticFilesFinder(BaseFinder):
    """Expose next-dj co-located assets under the `next/` staticfiles namespace."""

    def __init__(self) -> None:
        """Start with no held scan, built on the first lookup."""
        self._scan: _Scan | None = None

    def _current_scan(self) -> _Scan:
        """Return the held scan, rebuilding it when what it read has moved."""
        scan = self._scan
        if scan is None or _scan_stale(scan):
            scan = _build_scan()
            self._scan = scan
        return scan

    @overload
    def find(
        self, path: str, find_all: Literal[False] = ...
    ) -> str | None: ...  # pragma: no cover

    @overload
    def find(
        self, path: str, find_all: Literal[True]
    ) -> list[str]: ...  # pragma: no cover

    @overload
    def find(
        self, path: str, *, all: Literal[False]
    ) -> str | None: ...  # pragma: no cover

    @overload
    def find(
        self, path: str, *, all: Literal[True]
    ) -> list[str]: ...  # pragma: no cover

    @override
    def find(
        self, path: str, find_all: bool = False, **kwargs: bool
    ) -> str | list[str] | None:
        """Resolve the logical path to an absolute filesystem path or list."""
        # Django's BaseFinder.find dictates a positional bool and a deprecated
        # `all` keyword, so the override matches it and normalises `all` back.
        find_all = kwargs.get("all", find_all)
        source = self._current_scan().mapping.get(path)
        if source is None:
            return [] if find_all else None
        resolved = str(source)
        return [resolved] if find_all else resolved

    @override
    def list(
        self, ignore_patterns: Iterable[str] | None
    ) -> Iterator[tuple[str, Storage]]:
        """Yield logical-path and storage pairs for `collectstatic`."""
        patterns = list(ignore_patterns) if ignore_patterns is not None else []
        scan = self._current_scan()
        for logical_path in sorted(scan.mapping):
            if matches_patterns(logical_path, patterns):
                continue
            yield logical_path, scan.storage
