"""Django staticfiles finders for next-dj co-located assets and the client runtime.

Shares `PathResolver` with request-time discovery so both layers agree on every URL,
and caches the mapping until the same freshness token discovery uses goes stale.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final, NamedTuple, override

from django.apps import apps
from django.conf import settings
from django.contrib.staticfiles.finders import AppDirectoriesFinder, BaseFinder
from django.contrib.staticfiles.utils import matches_patterns
from django.core.files import File
from django.core.files.storage import Storage

from next.components import component_watch_roots, get_component_paths_for_watch
from next.conf.signals import settings_reloaded
from next.pages.watch import (
    get_layout_djx_paths_for_watch,
    get_pages_directories_for_watch,
    get_template_djx_paths_for_watch,
)
from next.utils import stat_mtime_ns, template_edits_watched

from .assets import StaticNamespace, default_kinds
from .discovery import PathResolver, default_stems, find_role_files
from .scripts import NEXT_JS_STATIC_PATH


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
    """Add the `{stem}.<kind>` files of a role directory to the output map.

    The probe is the one request-time discovery runs, so a logical name means one file.
    """
    for found in find_role_files(
        directory, logical_name=logical_name, role=role, stems=stems
    ):
        suffix = default_kinds.extension(found.kind)
        out.setdefault(
            f"{StaticNamespace.NEXT}/{found.logical_name}{suffix}", found.source_path
        )


def discover_colocated_static_assets() -> dict[str, Path]:
    """Map staticfiles logical paths to absolute source files on disk.

    The helper scans every configured page-backend tree plus registered components, and
    honors the stem and kind registries filled during `AppConfig.ready`.
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


_RUNTIME_BUNDLE_ROOT: Final = Path(__file__).parent
_RUNTIME_BUNDLE_PATHS: Final = (NEXT_JS_STATIC_PATH, f"{NEXT_JS_STATIC_PATH}.map")


def _runtime_bundle_source(logical_path: str) -> Path | None:
    """Return the built runtime file the logical path names, or None when unbuilt.

    Stat'd per lookup rather than held with a scan, because a source checkout builds
    the bundle while the process runs and a held miss would answer 404 until restart.
    """
    if logical_path not in _RUNTIME_BUNDLE_PATHS:
        return None
    source = _RUNTIME_BUNDLE_ROOT / logical_path
    return source if source.is_file() else None


def _runtime_bundle_static_files() -> dict[str, Path]:
    """Map the built client runtime and its sourcemap into the `next/` namespace.

    A source checkout carries no build output, so a missing file is left unmapped.
    """
    return {
        logical_path: source
        for logical_path in _RUNTIME_BUNDLE_PATHS
        if (source := _runtime_bundle_source(logical_path)) is not None
    }


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
        source = self._mapping.get(name)
        return source is not None and source.exists()

    @override
    def open(self, name: str, mode: str = "rb") -> File:
        """Open the file behind the logical name for reading."""
        path = self._resolve(name)
        return File(path.open(mode))

    @override
    def path(self, name: str) -> str:
        """Return the absolute filesystem path behind the logical name."""
        return str(self._resolve(name))

    @override
    def get_modified_time(self, name: str) -> datetime:
        """Return the mtime of the source file the way `FileSystemStorage` spells it.

        Without it `collectstatic` deletes and recopies the whole namespace every run.
        """
        stamp = self._resolve(name).stat().st_mtime
        return datetime.fromtimestamp(stamp, tz=UTC if settings.USE_TZ else None)

    @override
    def size(self, name: str) -> int:
        """Return the byte size of the source file behind the logical name."""
        return self._resolve(name).stat().st_size


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
    registries: tuple[int, int, int]
    watched: bool
    directories: tuple[tuple[Path, int | None], ...]


class _SettingsGeneration:
    """The reconfiguration counter every held scan carries.

    A reconfiguration moves what the walk finds and shifts no mtime the scan
    would notice, so it is counted rather than watched.
    """

    def __init__(self) -> None:
        """Start at the generation the first scan is stamped with."""
        self.value = 0

    def bump(self) -> None:
        """Mark every held scan stale."""
        self.value += 1


_settings_generation = _SettingsGeneration()


def _forget_scans(**kwargs) -> None:
    """Mark every held scan stale, so a reconfigure is walked again."""
    _settings_generation.bump()


settings_reloaded.connect(_forget_scans)


def _registry_generation() -> tuple[int, int, int]:
    """Return the generation of everything a scan reads while it is built.

    The placeholder registry is left out, because the finder names files rather than
    slots and no registration there moves which file a logical name means.
    """
    return (default_stems.version, default_kinds.version, _settings_generation.value)


def _scan_roots() -> _ScanRoots:
    """Return the page trees and the component trees a scan reads."""
    return _ScanRoots(
        tuple(get_pages_directories_for_watch()), tuple(component_watch_roots())
    )


_BYTECODE_CACHE_DIR = "__pycache__"


def _stat_directory(directory: Path) -> os.stat_result | None:
    """Return the stat of `directory`, or `None` when it does not stat."""
    try:
        return directory.stat()
    except OSError:
        return None


def _child_directories(directory: Path) -> list[Path]:
    """Return the directories held directly by `directory` that can hold an asset.

    A symlinked directory counts since the component glob reads through it. A
    bytecode cache is excluded so the scan's own imports cannot invalidate its answer.
    """
    try:
        with os.scandir(directory) as entries:
            return [
                Path(entry.path)
                for entry in entries
                if entry.is_dir()
                and entry.name != _BYTECODE_CACHE_DIR
                and not entry.name.startswith(".")
            ]
    except OSError:
        return []


def _scan_directories(roots: _ScanRoots) -> tuple[tuple[Path, int | None], ...]:
    """Snapshot the mtime of every directory the trees of `roots` hold.

    A missing directory records as `None`, so one appearing later rebuilds the scan.
    A directory reached by two configured trees is recorded once.
    """
    out: dict[Path, int | None] = {}
    seen: set[tuple[int, int]] = set()
    stack = [*roots.pages, *roots.components]
    while stack:
        directory = stack.pop()
        if directory in out:
            continue
        info = _stat_directory(directory)
        out[directory] = None if info is None else info.st_mtime_ns
        if info is None:
            continue
        key = (info.st_dev, info.st_ino)
        if key in seen:
            continue
        seen.add(key)
        stack.extend(_child_directories(directory))
    return tuple(out.items())


def _build_scan(roots: _ScanRoots) -> _Scan:
    """Discover every co-located asset and note what the answer was read from.

    Generations and the snapshot are taken before the walk, so anything landing
    mid-walk leaves the scan stale rather than falsely fresh.
    """
    registries = _registry_generation()
    watched = template_edits_watched()
    directories = _scan_directories(roots) if watched else ()
    mapping = discover_colocated_static_assets()
    return _Scan(
        mapping, _MappedSourceStorage(mapping), roots, registries, watched, directories
    )


def _scan_stale(scan: _Scan, roots: _ScanRoots) -> bool:
    """Whether anything the scan was read from has moved since.

    Generations and roots are always compared, directory mtimes only when the process
    watches template edits. A scan taken unwatched reads as stale once watching starts.
    """
    if scan.registries != _registry_generation():
        return True
    if scan.roots != roots:
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
    """Expose next-dj co-located assets under the `next/` staticfiles namespace.

    Discovered assets are held until the tree they were read from moves, while the
    client runtime bundle is a fixed pair of paths and is stat'd on every lookup.
    """

    def __init__(self) -> None:
        """Start with no held scan, built on the first lookup."""
        self._scan: _Scan | None = None

    def _current_scan(self) -> _Scan:
        """Return the held scan, rebuilding it when what it read has moved.

        The roots are read once, because reading them builds every configured router.
        """
        roots = _scan_roots()
        scan = self._scan
        if scan is None or _scan_stale(scan, roots):
            scan = _build_scan(roots)
            self._scan = scan
        return scan

    @override
    def find(  # type: ignore[override]
        self, path: str, find_all: bool = False, **kwargs: bool
    ) -> str | list[str]:
        """Resolve the logical path to an absolute path, or an empty list on a miss.

        A miss answers `[]` whatever `find_all` says, since `finders.find` reads another
        falsy answer as a match, and the ignore covers django-stubs typing it `str`.
        """
        # Django's BaseFinder.find dictates a positional bool and a deprecated
        # `all` keyword, so the override matches it and normalises `all` back.
        find_all = kwargs.get("all", find_all)
        source = self._current_scan().mapping.get(path) or _runtime_bundle_source(path)
        if source is None:
            return []
        resolved = str(source)
        return [resolved] if find_all else resolved

    @override
    def list(
        self, ignore_patterns: Iterable[str] | None
    ) -> Iterator[tuple[str, Storage]]:
        """Yield logical-path and storage pairs for `collectstatic`."""
        patterns = list(ignore_patterns) if ignore_patterns is not None else []
        scan = self._current_scan()
        bundle = _runtime_bundle_static_files()
        sources = ((scan.mapping, scan.storage), (bundle, _MappedSourceStorage(bundle)))
        for mapping, storage in sources:
            for logical_path in sorted(mapping):
                if matches_patterns(logical_path, patterns):
                    continue
                yield logical_path, storage


def _framework_app_name() -> str | None:
    """Return the app name of the framework's own `AppConfig`.

    Read from the app registry, so renaming the app cannot quietly republish it.
    """
    app_config = apps.get_containing_app_config(__name__)
    name: str | None = getattr(app_config, "name", None)
    return name


class NextAppDirectoriesFinder(AppDirectoriesFinder):
    """App static finder that leaves the framework's own package unpublished.

    `next/static` is the `next.static` package, so the stock finder serves its modules.
    """

    @override
    def __init__(self, app_names: Iterable[str] | None = None, *args, **kwargs) -> None:
        """Build the stock app storages, then drop the framework's own app."""
        super().__init__(app_names, *args, **kwargs)
        framework_app = _framework_app_name()
        self.apps = [name for name in self.apps if name != framework_app]
        self.storages = {
            name: storage
            for name, storage in self.storages.items()
            if name != framework_app
        }
