"""Pluggable router backend contract, file router, and backend factory."""

from __future__ import annotations

import functools
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast, override

from django.apps import apps
from django.core.exceptions import AppRegistryNotReady, ImproperlyConfigured

from next.backends import instantiate_backend, resolve_backend_class
from next.caches import DEFAULT_CACHE_SIZE
from next.conf import next_framework_settings
from next.intercept import Intercept, intercepts
from next.pages import page
from next.pages.loaders import load_page_module
from next.utils import (
    InterceptPage,
    PageRoot,
    classify_dirs_entries,
    is_within,
    on_forget_resolved_trees,
    resolve_base_dir,
    resolved_tree,
)

from .dispatcher import FilesystemTreeDispatcher
from .parser import default_url_parser
from .signals import intercept_registered, route_registered
from .urlconf import urlconf_mounts


if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Mapping

    from django.apps import AppConfig
    from django.urls import URLPattern, URLResolver

    from .urlconf import MountPoint, UrlconfMounts


logger = logging.getLogger(__name__)

# Django's own apps and the framework's `next/pages` package hold no project pages.
_NON_PAGE_APP_ROOTS = ("django", "next")

FILE_ROUTER_CONFIG_KEYS = ("PAGES_DIR", "APP_DIRS", "OPTIONS", "DIRS")
"""The keys a file router entry carries beside BACKEND, OPTIONS and DIRS optional."""

_REQUIRED_CONFIG_KEYS = ("PAGES_DIR", "APP_DIRS")
"""The keys an entry cannot leave out, since a partial entry is a settings mistake."""


def _is_framework_app(app_name: str) -> bool:
    """Whether the dotted app name belongs to Django or to next itself."""
    return any(
        app_name == root or app_name.startswith(f"{root}.")
        for root in _NON_PAGE_APP_ROOTS
    )


def is_plain_subdir(name: str) -> bool:
    """Whether `name` is one plain directory name, so a claim on it compares exactly."""
    return name not in {"", ".", ".."} and "/" not in name and "\\" not in name


def _tree_key(label: str, subdir: str) -> str:
    """Return the static key of an app tree, its app label and its pages directory."""
    return f"{label}/{subdir}"


@functools.lru_cache(maxsize=DEFAULT_CACHE_SIZE)
def _key_under(path: Path, base_dir: Path) -> str | None:
    """Return the posix path of a tree under the base directory, both resolved."""
    base = base_dir.resolve()
    tree = path.resolve()
    if tree == base:
        return base.name or None
    if tree.is_relative_to(base):
        return tree.relative_to(base).as_posix()
    return None


on_forget_resolved_trees(_key_under.cache_clear)


def _dirs_root_key(path: Path) -> str | None:
    """Return the static key of a `DIRS` root, its path under `BASE_DIR`.

    A root outside `BASE_DIR`, or any root while `BASE_DIR` is unset, has no key.
    """
    base_dir = resolve_base_dir()
    return None if base_dir is None else _key_under(path, base_dir)


class _InstalledApps(dict[str, Path]):
    """Each installed app's directory by dotted name, built once per registry snapshot.

    A stable map lets `page_roots` keep its answer until the registry changes.
    """

    __slots__ = ("configs", "labels")

    def __init__(self, configs: tuple[AppConfig, ...]) -> None:
        """Read the directory and the label of every app."""
        super().__init__((config.name, Path(config.path)) for config in configs)
        self.configs = configs
        self.labels = {config.name: config.label for config in configs}


class _InstalledAppsMemo:
    """The snapshot of the app registry the last read built."""

    def __init__(self) -> None:
        """Start with no snapshot."""
        self.held: _InstalledApps | None = None


_installed_apps = _InstalledAppsMemo()


def _installed_app_directories() -> _InstalledApps:
    """Map each installed app's dotted name to its directory.

    The registry is read per call, and the map is rebuilt only when its configs change.
    """
    try:
        configs = tuple(apps.get_app_configs())
    except AppRegistryNotReady:
        # A caller reached during app loading is told "no app pages".
        logger.warning(
            "the app registry was read before Django populated it, so no "
            "application page tree is reported for this call. Move the read "
            "into or after AppConfig.ready()."
        )
        return _InstalledApps(())
    held = _installed_apps.held
    if held is None or held.configs != configs:
        held = _installed_apps.held = _InstalledApps(configs)
    return held


class _RoutedView(Protocol):
    """A page view that knows the trail it was routed by inside its tree."""

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

        A backend routing from elsewhere reports none, so checks and watcher skip it.
        """
        return []

    def components_folder_name(self) -> str | None:
        """Folder name this backend registers components from while it walks.

        No component folder answers None, so no component glob is watched.
        """
        return None

    def skip_dir_names(self) -> frozenset[str]:
        """Directory names this backend's own walk of its trees refuses to enter.

        The checks walk with the same set, so they skip those directories too.
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


def _defines_render(page_file: Path) -> bool:
    """Return whether an intercept page defines `render()`, importing it as a route.

    The import registers its context, actions and access, as a routed page's does.
    """
    module, _error = load_page_module(page_file)
    return callable(getattr(module, "render", None))


class FileRouterBackend(RouterBackend):
    """Discover `page.py` (and virtual pages) under app and optional root trees."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        """Read the pages dir, the extra roots, and the narrowed OPTIONS off the entry.

        A subclass renaming the component folder overrides its resolver method.
        """
        self._require_config_keys(config)
        self.pages_dir = str(config["PAGES_DIR"])
        self.app_dirs = bool(config["APP_DIRS"])
        self.options = _narrow_file_router_options(config.get("OPTIONS"))

        components_dir = self._resolve_components_folder_name()
        # Already resolved, because the classification probes what it keeps.
        roots, segment_names = classify_dirs_entries(
            config.get("DIRS", []), resolve_base_dir()
        )
        self._extra_root_paths = roots
        self._skip_dir_names = frozenset({components_dir, *segment_names})
        self._components_folder_name = components_dir

        self._app_pages_path_cache: dict[str, tuple[Path, Path | None]] = {}
        self._tree_patterns_cache: dict[Path, list[URLPattern | URLResolver]] = {}
        self._root_patterns_cache: list[URLPattern | URLResolver] | None = None
        self._root_pages_paths_cache: list[Path] | None = None
        self._page_roots_cache: (
            tuple[_InstalledApps | None, UrlconfMounts, list[PageRoot]] | None
        ) = None
        self._url_parser = default_url_parser

    @classmethod
    def _require_config_keys(cls, config: Mapping[str, Any]) -> None:
        """Refuse an entry that leaves out one of the keys a file router reads."""
        for required in _REQUIRED_CONFIG_KEYS:
            if required not in config:
                rest = ", ".join(
                    key for key in _REQUIRED_CONFIG_KEYS if key != required
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
        return list(self._tree_urls(tree))

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
        roots = (
            [] if directories is None else self._app_roots(directories, mounts.claimed)
        )
        roots.extend(
            PageRoot(
                path=path,
                label="Root" if index == 0 else f"Root ({path})",
                key=_dirs_root_key(path),
            )
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
    ) -> Generator[tuple[str, Path], None, None]:
        """Yield `(app, path)` per app tree the auto-assembly routes at the root.

        A tree a `pages()` include claims is left to the URLconf that mounts it.
        """
        for app_name in self._get_installed_apps(directories):
            if (app_name, self.pages_dir) in claimed:
                continue
            path = self._get_app_pages_path(app_name, directories)
            if path is not None:
                yield app_name, path

    def _app_roots(
        self, directories: _InstalledApps, claimed: frozenset[tuple[str, str]]
    ) -> list[PageRoot]:
        """Return the app trees the auto-assembly routes at the root.

        Labels come from the snapshot that named the trees, so a custom label holds.
        """
        subdir = Path(self.pages_dir).as_posix()
        return [
            PageRoot(
                path=path,
                label=self._tree_label(app_name, self.pages_dir),
                key=_tree_key(directories.labels[app_name], subdir),
            )
            for app_name, path in self._app_trees(directories, claimed)
        ]

    def _mounted_root(self, point: MountPoint) -> PageRoot:
        """Return the page tree a `pages()` include of the URLconf mounts."""
        mount = point.mount
        return PageRoot(
            path=mount.path,
            label=self._tree_label(mount.name, mount.subdir),
            prefix=point.prefix,
            namespace=point.namespace,
            trail=point.trail,
            key=_tree_key(mount.app, Path(mount.subdir).as_posix()),
        )

    def _generate_app_urls(self) -> list[URLPattern | URLResolver]:
        """Return patterns from each installed app's `pages_dir` tree.

        One snapshot serves the pass, because reading it per app name rebuilds the map.
        """
        directories = _installed_app_directories()
        claimed = urlconf_mounts().claimed
        urls: list[URLPattern | URLResolver] = []
        for _app, path in self._app_trees(directories, claimed):
            urls.extend(self._tree_urls(path))
        return urls

    def _tree_urls(self, tree: Path) -> list[URLPattern | URLResolver]:
        """Return the cached patterns of one tree, scanning it on first use."""
        patterns = self._tree_patterns_cache.get(tree)
        if patterns is None:
            patterns = self._tree_patterns_cache[tree] = list(
                self._generate_patterns_from_directory(tree)
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
            if not _is_framework_app(app_name):
                yield app_name

    def _get_app_pages_path(
        self, app_name: str, directories: Mapping[str, Path]
    ) -> Path | None:
        """Return `<app>/pages_dir` when that directory exists.

        Memoised per app directory and dropped with this router.
        """
        app_path = directories.get(app_name)
        if app_path is None:
            return None
        # The directory sits beside the answer as a `str` key hashes cheaper.
        cached = self._app_pages_path_cache.get(app_name)
        if cached is not None and cached[0] == app_path:
            return cached[1]
        candidate = app_path / self.pages_dir
        pages_path = resolved_tree(candidate) if candidate.exists() else None
        self._app_pages_path_cache[app_name] = (app_path, pages_path)
        return pages_path

    def _get_root_pages_paths(self) -> list[Path]:
        """Return paths from `DIRS` plus optional `BASE_DIR` / `pages_dir`.

        Memoised per instance and a copy goes back, so appending moves no root.
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
        self, pages_path: Path
    ) -> Generator[URLPattern, None, None]:
        """Yield one `URLPattern` per discovered page under `pages_path`.

        Each view keeps its trail for crumbs, and the intercepts publish after the walk.
        """
        generation = intercepts.generation
        found: list[InterceptPage] = []
        routed: dict[str, Path] = {}
        for url_path, file_path in self._scan_pages_directory(
            pages_path, on_intercept=found.append
        ):
            if pattern := self._page_pattern(url_path, file_path):
                cast("_RoutedView", pattern.callback).next_route_trail = url_path
                route_registered.send(
                    sender=FileRouterBackend, url_path=url_path, file_path=file_path
                )
                routed.setdefault(url_path, file_path)
                yield pattern
        self._publish_intercepts(pages_path, found, routed, generation)

    def _publish_intercepts(
        self,
        pages_path: Path,
        found: list[InterceptPage],
        routed: dict[str, Path],
        generation: int,
    ) -> None:
        """Publish every intercept of `pages_path` over a page the same walk routed.

        A tree with none publishes an empty set, so a reload drops its vanished rules.
        """
        nested = self._nested_roots(pages_path) if found else ()
        rules: list[Intercept] = []
        for hit in found:
            target_file = routed.get(hit.target_trail)
            if target_file is None or any(is_within(hit.file, root) for root in nested):
                continue
            if _defines_render(hit.file):
                logger.warning(
                    "The intercept page %s defines render(), which no zone request "
                    "runs, so it is not served. manage.py check reports it.",
                    hit.file,
                )
                continue
            rules.append(Intercept(target_file, hit.file, hit.source, hit.marker))
        if not intercepts.publish(pages_path, rules, generation=generation):
            return
        for rule in rules:
            intercept_registered.send(
                sender=FileRouterBackend,
                target_file=rule.target_file,
                file_path=rule.file,
                marker=rule.marker,
            )

    def _nested_roots(self, pages_path: Path) -> tuple[Path, ...]:
        """Return the page trees this router reports strictly inside `pages_path`."""
        return tuple(
            root.path
            for root in self.page_roots()
            if root.path != pages_path and is_within(root.path, pages_path)
        )

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
        self,
        pages_path: Path,
        *,
        register_components: bool = True,
        on_intercept: Callable[[InterceptPage], None] | None = None,
    ) -> Generator[tuple[str, Path], None, None]:
        """Yield `(url_path, page_file)` pairs discovered under `pages_path`."""
        dispatcher = FilesystemTreeDispatcher(
            self._skip_dir_names,
            components_folder_name=self._components_folder_name,
            register_components=register_components,
        )
        yield from dispatcher.walk(pages_path, on_intercept=on_intercept)


class RouterFactory:
    """Build one `RouterBackend` from one `PAGE_BACKENDS` entry.

    `RouterManager` loads the list through `next.backends`, this is the watcher's build.
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
