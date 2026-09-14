"""Pluggable router backend contract, file router, and backend factory."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

from django.apps import apps
from django.core.exceptions import AppRegistryNotReady, ImproperlyConfigured

from next.backends import instantiate_backend, resolve_backend_class
from next.conf import next_framework_settings
from next.pages import page
from next.utils import PageRoot, classify_dirs_entries, resolve_base_dir, resolved_tree

from .dispatcher import FilesystemTreeDispatcher
from .parser import default_url_parser
from .signals import route_registered


if TYPE_CHECKING:
    from collections.abc import Generator, Mapping

    from django.urls import URLPattern, URLResolver


logger = logging.getLogger(__name__)

# Django's own apps and the framework package hold no project pages, and the
# framework ships a `next/pages` package that otherwise reads as a page tree.
_NON_PAGE_APP_ROOTS = ("django", "next")

# The keys a file router entry carries beside BACKEND, read here and validated by
# the checks. They have no defaults, because a partial entry is a settings mistake
# the checks name rather than a shorthand.
FILE_ROUTER_CONFIG_KEYS = ("PAGES_DIR", "APP_DIRS", "OPTIONS", "DIRS")


def _is_framework_app(app_name: str) -> bool:
    """Whether the dotted app name belongs to Django or to next itself."""
    return any(
        app_name == root or app_name.startswith(f"{root}.")
        for root in _NON_PAGE_APP_ROOTS
    )


def _installed_app_directories() -> dict[str, Path]:
    """Map each installed app's dotted name to its directory.

    Read live on every call, because `INSTALLED_APPS` changes without a settings reload.
    """
    try:
        configs = apps.get_app_configs()
    except AppRegistryNotReady:
        # Answering keeps a caller reached during app loading alive, but the
        # answer reads as "this project has no app pages", so it is named.
        logger.warning(
            "the app registry was read before Django populated it, so no "
            "application page tree is reported for this call. Move the read "
            "into or after AppConfig.ready()."
        )
        return {}
    return {config.name: Path(config.path) for config in configs}


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

        self._patterns_cache: dict[str, list[URLPattern | URLResolver]] = {}
        self._app_pages_path_cache: dict[str, tuple[Path, Path | None]] = {}
        self._root_patterns_cache: list[URLPattern | URLResolver] | None = None
        self._root_pages_paths_cache: list[Path] | None = None
        self._url_parser = default_url_parser

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

    @override
    def page_roots(self) -> list[PageRoot]:
        """Report the app trees and root trees this router serves from.

        The app reads share one snapshot, because the finder and the reloader ask often.
        """
        roots: list[PageRoot] = []
        if self.app_dirs:
            directories = _installed_app_directories()
            roots.extend(
                PageRoot(path=app_path, label=f"App '{app_name}'")
                for app_name in self._get_installed_apps(directories)
                if (app_path := self._get_app_pages_path(app_name, directories))
                is not None
            )
        roots.extend(
            PageRoot(path=path, label="Root" if index == 0 else f"Root ({path})")
            for index, path in enumerate(self._get_root_pages_paths())
        )
        return roots

    def _generate_app_urls(self) -> list[URLPattern | URLResolver]:
        """Return patterns from each installed app's `pages_dir` tree.

        One snapshot serves the pass, because reading it per app name rebuilds the map.
        """
        directories = _installed_app_directories()
        urls: list[URLPattern | URLResolver] = []
        for app_name in self._get_installed_apps(directories):
            if patterns := self._generate_urls_for_app(app_name, directories):
                urls.extend(patterns)
        return urls

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

    def _generate_urls_for_app(
        self, app_name: str, directories: Mapping[str, Path]
    ) -> list[URLPattern | URLResolver]:
        """Return cached patterns for one app, scanning on first use."""
        if app_name in self._patterns_cache:
            return self._patterns_cache[app_name]
        if pages_path := self._get_app_pages_path(app_name, directories):
            patterns: list[URLPattern | URLResolver] = list(
                self._generate_patterns_from_directory(pages_path)
            )
            self._patterns_cache[app_name] = patterns
            return patterns
        return []

    def _generate_patterns_from_directory(
        self, pages_path: Path
    ) -> Generator[URLPattern, None, None]:
        """Yield one `URLPattern` per discovered page under `pages_path`."""
        for url_path, file_path in self._scan_pages_directory(pages_path):
            if pattern := page.create_url_pattern(
                url_path, file_path, self._url_parser
            ):
                route_registered.send(
                    sender=FileRouterBackend, url_path=url_path, file_path=file_path
                )
                yield pattern

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
