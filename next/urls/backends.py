"""Pluggable router backend contract, file router, and backend factory."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast, override

from django.apps import apps
from django.core.exceptions import AppRegistryNotReady, ImproperlyConfigured

from next.backends import instantiate_backend, resolve_backend_class
from next.conf import next_framework_settings
from next.pages import page
from next.utils import (
    PageRoot,
    classify_dirs_entries,
    join_trail,
    prefix_trail,
    resolve_base_dir,
    resolved_tree,
)

from .dispatcher import FilesystemTreeDispatcher
from .parser import default_url_parser
from .signals import route_registered
from .urlconf import urlconf_mounts


if TYPE_CHECKING:
    from collections.abc import Generator, Mapping

    from django.apps import AppConfig
    from django.urls import URLPattern, URLResolver

    from .urlconf import MountPoint, UrlconfMounts


logger = logging.getLogger(__name__)

# Django's own apps and the framework package hold no project pages, and the
# framework ships a `next/pages` package that otherwise reads as a page tree.
_NON_PAGE_APP_ROOTS = ("django", "next")

# The keys a file router entry carries beside BACKEND, read here and validated by
# the checks. They have no defaults, because a partial entry is a settings mistake
# the checks name rather than a shorthand.
FILE_ROUTER_CONFIG_KEYS = ("PAGES_DIR", "APP_DIRS", "OPTIONS", "DIRS")


def is_framework_app(app_name: str) -> bool:
    """Whether the dotted app name belongs to Django or to next itself."""
    return any(
        app_name == root or app_name.startswith(f"{root}.")
        for root in _NON_PAGE_APP_ROOTS
    )


type MountedTrees = tuple[tuple[str | None, str], ...]
"""The `(subdir, prefix)` pairs one app mounts, `None` for the router's `PAGES_DIR`."""


@dataclass(frozen=True, slots=True)
class MountDeclaration:
    """What one app declares through `mount` or `mounts`, or why the router refuses it.

    A refused declaration routes like no declaration at all, and the checks name it.
    """

    trees: MountedTrees | None = None
    refusal: str | None = None
    bracketed: tuple[str, ...] = ()


_UNDECLARED = MountDeclaration()


def _normalise_prefix(prefix: str) -> str:
    """Return a prefix with one trailing slash and no leading one, or empty."""
    stripped = prefix.strip("/")
    return f"{stripped}/" if stripped else ""


def is_plain_subdir(name: str) -> bool:
    """Whether `name` is one plain directory name, so a claim on it compares exactly."""
    return name not in {"", ".", ".."} and "/" not in name and "\\" not in name


def _declared_prefixes(config: AppConfig) -> dict[str | None, str] | str | None:
    """Return the subdirectory prefixes an app declares, the refusal, or `None`."""
    mount: object = getattr(config, "mount", None)
    mounts: object = getattr(config, "mounts", None)
    if mount is None and mounts is None:
        return None
    if mount is not None and mounts is not None:
        return "declares both mount and mounts"
    if mount is not None:
        if not isinstance(mount, str):
            return f"declares mount as {type(mount).__name__} instead of a str"
        return {None: mount}
    return _mounts_refusal(mounts) or cast("dict[str | None, str]", mounts)


def _mounts_refusal(mounts: object) -> str | None:
    """Return why a `mounts` value is refused, `None` for a valid one."""
    if not isinstance(mounts, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in mounts.items()
    ):
        return "declares mounts as something other than a dict of str to str"
    unplain = [key for key in mounts if not is_plain_subdir(key)]
    if unplain:
        return f"declares mounts keys {unplain!r}, which are no plain directory names"
    return None


def read_mount_declaration(config: AppConfig) -> MountDeclaration:
    """Return the page trees `config` mounts through `mount` or `mounts`.

    `mount` moves the router's `PAGES_DIR` tree, and a bracket in a prefix is refused.
    """
    declared = _declared_prefixes(config)
    if declared is None:
        return _UNDECLARED
    if isinstance(declared, str):
        return MountDeclaration(refusal=declared)
    bracketed = tuple(
        prefix for prefix in declared.values() if "[" in prefix or "]" in prefix
    )
    if bracketed:
        return MountDeclaration(bracketed=bracketed)
    return MountDeclaration(
        trees=tuple(
            (subdir, _normalise_prefix(prefix)) for subdir, prefix in declared.items()
        )
    )


class _InstalledApps(dict[str, Path]):
    """Each installed app's directory by dotted name, with what each app declares.

    Built once per app registry snapshot, since a declaration is a class attribute.
    """

    __slots__ = ("configs", "labels", "mounted")

    def __init__(self, configs: tuple[AppConfig, ...]) -> None:
        """Read the directory, the label and the mounted trees of every app."""
        super().__init__((config.name, Path(config.path)) for config in configs)
        self.configs = configs
        self.labels = {config.name: config.label for config in configs}
        self.mounted: dict[str, MountedTrees] = {
            config.name: trees
            for config in configs
            if not is_framework_app(config.name)
            and (trees := read_mount_declaration(config).trees) is not None
        }


class _InstalledAppsMemo:
    """The snapshot of the app registry the last read built."""

    def __init__(self) -> None:
        """Start with no snapshot."""
        self.held: _InstalledApps | None = None


_installed_apps = _InstalledAppsMemo()


def _installed_app_directories() -> dict[str, Path]:
    """Map each installed app's dotted name to its directory.

    The registry is read per call, and the map is rebuilt only when its configs change.
    """
    try:
        configs = tuple(apps.get_app_configs())
    except AppRegistryNotReady:
        # Answering keeps a caller reached during app loading alive, but the
        # answer reads as "this project has no app pages", so it is named.
        logger.warning(
            "the app registry was read before Django populated it, so no "
            "application page tree is reported for this call. Move the read "
            "into or after AppConfig.ready()."
        )
        return {}
    held = _installed_apps.held
    if held is None or held.configs != configs:
        held = _installed_apps.held = _InstalledApps(configs)
    return held


def _mounted_trees(
    app_name: str, directories: Mapping[str, Path]
) -> MountedTrees | None:
    """Return the trees `app_name` declares, `None` when it declares none."""
    if isinstance(directories, _InstalledApps):
        return directories.mounted.get(app_name)
    return None


def _app_label(app_name: str, directories: Mapping[str, Path]) -> str:
    """Return the label of `app_name`, the last part of its name when unknown."""
    if isinstance(directories, _InstalledApps):
        return directories.labels.get(app_name, app_name)
    return app_name.rpartition(".")[2]


class _MountedView(Protocol):
    """A page view that knows the trail it was routed by and the prefix inside it."""

    next_route_prefix: str
    next_route_trail: str


class RouterBackend(ABC):
    """Pluggable source of `URLPattern` and `URLResolver` entries.

    A backend takes its own `PAGE_BACKENDS` entry as its single constructor argument.
    """

    @abstractmethod
    def generate_urls(self) -> list[URLPattern | URLResolver]:
        """Patterns contributed by this backend to the project URLconf."""

    def page_roots(self) -> list[PageRoot]:
        """Labelled page trees this backend routes, for system checks to walk.

        A backend that routes from somewhere else reports none, which leaves it
        out of every page-tree check and out of the development watcher.
        """
        return []

    def components_folder_name(self) -> str | None:
        """Folder name this backend registers components from while it walks.

        A backend that registers no component folder answers None, and the
        development watcher then watches no component glob under its trees.
        """
        return None

    def skip_dir_names(self) -> frozenset[str]:
        """Directory names this backend's own walk of its trees refuses to enter.

        The checks walk with the same set, so a directory this backend routes
        nothing from is a directory they report nothing from either.
        """
        return frozenset()


def _narrow_file_router_options(options: object) -> dict[str, Any]:
    """Keep only keys consumed by `next.pages` (e.g. `context_processors`)."""
    if not isinstance(options, dict):
        return {}
    cp = options.get("context_processors")
    if not isinstance(cp, list):
        cp = []
    cp = [x for x in cp if isinstance(x, str)]
    if not cp:
        return {}
    return {"context_processors": cp}


class FileRouterBackend(RouterBackend):
    """Discover `page.py` (and virtual pages) under app and optional root trees."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        """Read the pages dir, the extra roots, and the narrowed OPTIONS off the entry.

        A subclass renaming the component folder overrides
        `_resolve_components_folder_name`, which is read here and nowhere else.
        """
        self._require_config_keys(config)
        self.pages_dir = str(config["PAGES_DIR"])
        self.app_dirs = bool(config["APP_DIRS"])
        self.options = _narrow_file_router_options(config["OPTIONS"])

        components_dir = self._resolve_components_folder_name()
        # Already resolved, because the classification probes what it keeps.
        roots, segment_names = classify_dirs_entries(config["DIRS"], resolve_base_dir())
        self._extra_root_paths = roots
        self._skip_dir_names = frozenset({components_dir, *segment_names})
        self._components_folder_name = components_dir

        self._app_pages_path_cache: dict[str, tuple[Path, Path | None]] = {}
        self._app_tree_path_cache: dict[tuple[str, str], tuple[Path, Path | None]] = {}
        self._tree_patterns_cache: dict[
            tuple[Path, str, bool], list[URLPattern | URLResolver]
        ] = {}
        self._root_patterns_cache: list[URLPattern | URLResolver] | None = None
        self._root_pages_paths_cache: list[Path] | None = None
        self._page_roots_cache: (
            tuple[Mapping[str, Path] | None, UrlconfMounts, list[PageRoot]] | None
        ) = None
        self._url_parser = default_url_parser
        self._default_trees: MountedTrees = ((self.pages_dir, ""),)

    @classmethod
    def _require_config_keys(cls, config: Mapping[str, Any]) -> None:
        """Refuse an entry that leaves out one of the keys a file router reads."""
        for required in FILE_ROUTER_CONFIG_KEYS:
            if required not in config:
                rest = ", ".join(
                    key for key in FILE_ROUTER_CONFIG_KEYS if key != required
                )
                msg = (
                    f"A {cls.__name__} entry needs {required!r} alongside "
                    f"{rest}, got {config!r}."
                )
                raise ImproperlyConfigured(msg)

    @override
    def components_folder_name(self) -> str | None:
        """Return the folder name the tree walk registers components from."""
        return self._components_folder_name

    @override
    def skip_dir_names(self) -> frozenset[str]:
        """Return the directory names this router's own tree walk refuses."""
        return self._skip_dir_names

    @staticmethod
    def _resolve_components_folder_name() -> str:
        """Folder name to skip in URL scans.

        Taken from the first `COMPONENT_BACKENDS` entry.
        """
        cbs = next_framework_settings.COMPONENT_BACKENDS
        cb0 = cbs[0] if isinstance(cbs, list) and cbs else None
        if not isinstance(cb0, dict) or "COMPONENTS_DIR" not in cb0:
            msg = (
                "A file router takes the folder it skips from COMPONENTS_DIR on "
                f"the first NEXT_FRAMEWORK['COMPONENT_BACKENDS'] entry, got {cbs!r}."
            )
            raise ImproperlyConfigured(msg)
        return str(cb0["COMPONENTS_DIR"])

    @override
    def __repr__(self) -> str:
        """Debug representation."""
        return (
            f"<{self.__class__.__name__} pages_dir='{self.pages_dir}' "
            f"app_dirs={self.app_dirs}>"
        )

    @override
    def generate_urls(self) -> list[URLPattern | URLResolver]:
        """Yield app routes first when `app_dirs` is set, then root `pages` dirs."""
        urls = self._generate_app_urls() if self.app_dirs else []
        urls.extend(self._generate_root_urls())
        return urls

    def generate_tree_urls(self, tree: Path) -> list[URLPattern | URLResolver]:
        """Return the patterns of a tree a URLconf mounts through `pages()`.

        The routes stay relative to the tree, since Django joins in the outer prefix.
        """
        return list(self._tree_urls(tree, "", mounted=True))

    @override
    def page_roots(self) -> list[PageRoot]:
        """Report the app trees, the root trees and the trees a URLconf mounts.

        Kept per app snapshot and URLconf walk, since the finder and reloader ask often.
        """
        mounts = urlconf_mounts()
        directories = _installed_app_directories() if self.app_dirs else None
        held = self._page_roots_cache
        if held is not None and held[0] is directories and held[1] is mounts:
            return list(held[2])
        roots = [] if directories is None else self._app_roots(directories, mounts)
        roots.extend(
            PageRoot(path=path, label="Root" if index == 0 else f"Root ({path})")
            for index, path in enumerate(self._get_root_pages_paths())
        )
        roots.extend(self._mounted_root(point) for point in mounts.points)
        self._page_roots_cache = (directories, mounts, roots)
        return list(roots)

    def _tree_label(self, app_name: str, subdir: str) -> str:
        """Return the label a report names one app tree by."""
        if subdir == self.pages_dir:
            return f"App '{app_name}'"
        return f"App '{app_name}' ({subdir})"

    def _app_trees(
        self, directories: Mapping[str, Path], claimed: frozenset[tuple[str, str]]
    ) -> Generator[tuple[str, str, str, Path, bool], None, None]:
        """Yield `(app, subdir, prefix, path, declared)` per auto-assembled app tree."""
        for app_name in self._get_installed_apps(directories):
            declared = _mounted_trees(app_name, directories)
            for key, prefix in self._default_trees if declared is None else declared:
                subdir = self.pages_dir if key is None else key
                if (app_name, subdir) in claimed:
                    continue
                path = self._get_app_tree_path(app_name, directories, subdir)
                if path is not None:
                    yield app_name, subdir, prefix, path, declared is not None

    def _app_roots(
        self, directories: Mapping[str, Path], mounts: UrlconfMounts
    ) -> list[PageRoot]:
        """Return the app trees the auto-assembly routes, the prefixed ones first."""
        prefixed: list[PageRoot] = []
        plain: list[PageRoot] = []
        for app_name, subdir, prefix, path, declared in self._app_trees(
            directories, mounts.claimed
        ):
            (prefixed if prefix else plain).append(
                PageRoot(
                    path=path,
                    label=self._tree_label(app_name, subdir),
                    prefix=prefix,
                    subdir=subdir,
                    app=_app_label(app_name, directories),
                    source="mounts" if declared else "root",
                )
            )
        return [*prefixed, *plain]

    def _mounted_root(self, point: MountPoint) -> PageRoot:
        """Return the page tree a `pages()` include of the URLconf mounts."""
        mount = point.mount
        return PageRoot(
            path=mount.path,
            label=self._tree_label(mount.name, mount.subdir),
            prefix=point.prefix,
            subdir=mount.subdir,
            app=mount.app,
            source="urls",
            namespace=mount.namespace,
        )

    def _generate_app_urls(self) -> list[URLPattern | URLResolver]:
        """Return patterns from each installed app's trees, the prefixed ones first.

        One snapshot serves the pass, because reading it per app name rebuilds the map.
        """
        directories = _installed_app_directories()
        claimed = urlconf_mounts().claimed
        prefixed: list[URLPattern | URLResolver] = []
        plain: list[URLPattern | URLResolver] = []
        for _app, _subdir, prefix, path, _declared in self._app_trees(
            directories, claimed
        ):
            trail = prefix_trail(prefix)
            (prefixed if prefix else plain).extend(
                self._tree_urls(path, trail, mounted=bool(trail))
            )
        return [*prefixed, *plain]

    def _tree_urls(
        self, tree: Path, prefix: str, *, mounted: bool
    ) -> list[URLPattern | URLResolver]:
        """Return the cached patterns of one tree routed under the trail `prefix`."""
        key = (tree, prefix, mounted)
        patterns = self._tree_patterns_cache.get(key)
        if patterns is None:
            patterns = self._tree_patterns_cache[key] = list(
                self._generate_patterns_from_directory(
                    tree, prefix=prefix, mounted=mounted
                )
            )
        return patterns

    def _generate_root_urls(self) -> list[URLPattern | URLResolver]:
        """Return cached patterns from each configured root pages directory.

        Returns a copy so a caller appending to the result never mutates the cache.
        """
        if self._root_patterns_cache is None:
            urls: list[URLPattern | URLResolver] = []
            for pages_path in self._get_root_pages_paths():
                urls.extend(self._generate_patterns_from_directory(pages_path))
            self._root_patterns_cache = urls
        return list(self._root_patterns_cache)

    def _get_installed_apps(
        self, directories: Mapping[str, Path]
    ) -> Generator[str, None, None]:
        """Yield the dotted name of every installed app that can hold pages.

        The snapshot comes from the caller, so nothing below opens the registry again.
        """
        for app_name in directories:
            if not is_framework_app(app_name):
                yield app_name

    def _get_app_pages_path(
        self, app_name: str, directories: Mapping[str, Path]
    ) -> Path | None:
        """Return `<app>/pages_dir` when that directory exists.

        Memoised per app directory, so a static lookup pays no probe per call. The memo
        dies with this router, which the watch layer rebuilds per read.
        """
        app_path = directories.get(app_name)
        if app_path is None:
            return None
        # The directory sits beside the answer rather than in the key, because
        # this runs per static lookup and a `str` key hashes cheaper.
        cached = self._app_pages_path_cache.get(app_name)
        if cached is not None and cached[0] == app_path:
            return cached[1]
        candidate = app_path / self.pages_dir
        pages_path = resolved_tree(candidate) if candidate.exists() else None
        self._app_pages_path_cache[app_name] = (app_path, pages_path)
        return pages_path

    def _get_app_tree_path(
        self, app_name: str, directories: Mapping[str, Path], subdir: str
    ) -> Path | None:
        """Return `<app>/subdir` when that directory exists, memoised per router."""
        if subdir == self.pages_dir:
            return self._get_app_pages_path(app_name, directories)
        app_path = directories.get(app_name)
        if app_path is None:
            return None
        key = (app_name, subdir)
        cached = self._app_tree_path_cache.get(key)
        if cached is not None and cached[0] == app_path:
            return cached[1]
        candidate = app_path / subdir
        tree = resolved_tree(candidate) if candidate.is_dir() else None
        self._app_tree_path_cache[key] = (app_path, tree)
        return tree

    def _get_root_pages_paths(self) -> list[Path]:
        """Return paths from `DIRS` plus optional `BASE_DIR` / `pages_dir`.

        Memoised per instance, because the roots belong to this router's configuration
        and every reader asks per call. A copy goes back, so appending moves no root.
        """
        if self._root_pages_paths_cache is None:
            result = [p for p in self._extra_root_paths if p.exists()]
            if not self.app_dirs and not result:
                base_dir = resolve_base_dir()
                if base_dir is not None:
                    pages_path = base_dir / self.pages_dir
                    if pages_path.exists():
                        result.append(resolved_tree(pages_path))
            self._root_pages_paths_cache = result
        return list(self._root_pages_paths_cache)

    def _generate_patterns_from_directory(
        self, pages_path: Path, *, prefix: str = "", mounted: bool = False
    ) -> Generator[URLPattern, None, None]:
        """Yield one `URLPattern` per discovered page under `pages_path`.

        A `mounted` view keeps its prefix and trail, so its crumbs reverse anywhere.
        """
        for url_path, file_path in self._scan_pages_directory(pages_path):
            trail = join_trail(prefix, url_path)
            if pattern := self._page_pattern(trail, file_path):
                if mounted:
                    view = cast("_MountedView", pattern.callback)
                    view.next_route_prefix = prefix
                    view.next_route_trail = trail
                route_registered.send(
                    sender=FileRouterBackend, url_path=trail, file_path=file_path
                )
                yield pattern

    def _page_pattern(self, trail: str, file_path: Path) -> URLPattern | None:
        """Return the pattern of one page, `None` for a trail the parser refuses.

        A refused page stays unrouted rather than abort the URLconf, so the checks run.
        """
        try:
            return page.create_url_pattern(trail, file_path, self._url_parser)
        except self._url_parser.parameter_error as exc:
            logger.warning(
                "%s The page is not routed, and manage.py check reports it.", exc
            )
            return None

    def _scan_pages_directory(
        self, pages_path: Path, *, register_components: bool = True
    ) -> Generator[tuple[str, Path], None, None]:
        """Yield `(url_path, page_file)` pairs discovered under `pages_path`."""
        dispatcher = FilesystemTreeDispatcher(
            self._skip_dir_names,
            components_folder_name=self._components_folder_name,
            register_components=register_components,
        )
        yield from dispatcher.walk(pages_path)


class RouterFactory:
    """Build one `RouterBackend` from one `PAGE_BACKENDS` entry.

    `RouterManager` loads the whole list through `next.backends`. This is the
    single-entry build the router access port hands the development watcher.
    """

    @classmethod
    def create_backend(cls, config: Mapping[str, Any]) -> RouterBackend:
        """Return the router named by `config['BACKEND']`, built from the entry.

        Every router of the family takes the entry, so nothing here knows one of them.
        """
        return instantiate_backend(
            resolve_backend_class(config, base=RouterBackend), config
        )


__all__ = ["FileRouterBackend", "RouterBackend", "RouterFactory"]
