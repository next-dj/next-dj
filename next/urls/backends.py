"""Pluggable router backend contract, file router, and backend factory."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, override

from django.apps import apps
from django.core.exceptions import AppRegistryNotReady

from next.conf import import_class_cached, next_framework_settings
from next.pages import page
from next.utils import PageRoot, classify_dirs_entries, resolve_base_dir

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


def _is_framework_app(app_name: str) -> bool:
    """Whether the dotted app name belongs to Django or to next itself."""
    return any(
        app_name == root or app_name.startswith(f"{root}.")
        for root in _NON_PAGE_APP_ROOTS
    )


def _installed_app_directories() -> dict[str, Path]:
    """Map each installed app's dotted name to its directory.

    Read live on every call, because `INSTALLED_APPS` changes without the
    settings reload that rebuilds a backend.
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
    """Pluggable source of `URLPattern` and `URLResolver` entries."""

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


def _narrow_file_router_options(options: dict[str, Any]) -> dict[str, Any]:
    """Keep only keys consumed by `next.pages` (e.g. `context_processors`)."""
    cp = options.get("context_processors")
    if not isinstance(cp, list):
        cp = []
    cp = [x for x in cp if isinstance(x, str)]
    if not cp:
        return {}
    return {"context_processors": cp}


class FileRouterBackend(RouterBackend):
    """Discover `page.py` (and virtual pages) under app and optional root trees."""

    DEFAULT_COMPONENTS_FOLDER_NAME: ClassVar[str] = "_components"

    def __init__(
        self,
        pages_dir: str | None = None,
        *,
        app_dirs: bool | None = None,
        extra_root_paths: list[Path] | None = None,
        skip_dir_names: frozenset[str] | None = None,
        components_folder_name: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        """Configure pages dir, extra roots, skip-dir names, and narrowed OPTIONS."""
        self.pages_dir = pages_dir if pages_dir is not None else "pages"
        self.app_dirs = app_dirs if app_dirs is not None else True
        raw_opts = dict(options) if options else {}
        base_dir = resolve_base_dir()

        comp_name = (
            self.DEFAULT_COMPONENTS_FOLDER_NAME
            if components_folder_name is None
            else components_folder_name
        )
        if extra_root_paths is None or skip_dir_names is None:
            dirs_list = list(extra_root_paths or [])
            path_roots, segment_names = classify_dirs_entries(dirs_list, base_dir)
            roots: list[Path] = path_roots
            skip = frozenset({comp_name, *segment_names})
        else:
            roots = list(extra_root_paths)
            skip = skip_dir_names
        self._extra_root_paths = roots
        self._skip_dir_names = skip
        self._components_folder_name = comp_name

        self.options = _narrow_file_router_options(raw_opts)
        self._patterns_cache: dict[str, list[URLPattern | URLResolver]] = {}
        self._app_pages_path_cache: dict[str, tuple[Path, Path | None]] = {}
        self._root_patterns_cache: list[URLPattern | URLResolver] | None = None
        self._root_pages_paths_cache: list[Path] | None = None
        self._url_parser = default_url_parser

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
        _components_key = "COMPONENTS_DIR"
        if not isinstance(cbs, list) or not cbs:
            raise KeyError(_components_key)
        cb0 = cbs[0]
        if not isinstance(cb0, dict) or _components_key not in cb0:
            raise KeyError(_components_key)
        return str(cb0[_components_key])

    @override
    def __repr__(self) -> str:
        """Debug representation."""
        return (
            f"<{self.__class__.__name__} pages_dir='{self.pages_dir}' "
            f"app_dirs={self.app_dirs}>"
        )

    @override
    def __eq__(self, other: object) -> bool:
        """Return True when the other backend has the same pages configuration."""
        if not isinstance(other, FileRouterBackend):
            return False
        return (
            self.pages_dir == other.pages_dir
            and self.app_dirs == other.app_dirs
            and self.options == other.options
            and self._extra_root_paths == other._extra_root_paths
            and self._skip_dir_names == other._skip_dir_names
            and self._components_folder_name == other._components_folder_name
        )

    @override
    def __hash__(self) -> int:
        """Hash from pages config including extra roots and skip names."""
        cp = self.options.get("context_processors")
        cp_t = tuple(cp) if isinstance(cp, list) else ()
        return hash(
            (
                self.pages_dir,
                self.app_dirs,
                tuple(self._extra_root_paths),
                tuple(sorted(self._skip_dir_names)),
                self._components_folder_name,
                cp_t,
            )
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

        The app reads share one registry snapshot, because a page-root listing
        sits on the static finder and reloader paths.
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

        One registry snapshot serves the whole pass, because reading it per
        app name rebuilds the map per app.
        """
        directories = _installed_app_directories()
        urls: list[URLPattern | URLResolver] = []
        for app_name in self._get_installed_apps(directories):
            if patterns := self._generate_urls_for_app(app_name, directories):
                urls.extend(patterns)
        return urls

    def _generate_root_urls(self) -> list[URLPattern | URLResolver]:
        """Return cached patterns from each configured root pages directory.

        Returns a copy so callers appending to `generate_urls` results
        never mutate the cache.
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

        The registry snapshot comes from the caller that opened the pass, so
        nothing below it reads the registry again.
        """
        for app_name in directories:
            if not _is_framework_app(app_name):
                yield app_name

    def _get_app_pages_path(
        self, app_name: str, directories: Mapping[str, Path]
    ) -> Path | None:
        """Return `<app>/pages_dir` when that directory exists.

        The `exists()` answer is memoised per app directory, so an app that
        moves is looked at again while a tree created after the first read
        needs the fresh backend a settings reload builds.
        """
        app_path = directories.get(app_name)
        if app_path is None:
            return None
        # The directory sits beside the answer rather than in the key, because
        # this runs per static lookup and a `str` key hashes cheaper than a
        # `Path` one.
        cached = self._app_pages_path_cache.get(app_name)
        if cached is not None and cached[0] == app_path:
            return cached[1]
        pages_path = app_path / self.pages_dir
        result = pages_path if pages_path.exists() else None
        self._app_pages_path_cache[app_name] = (app_path, result)
        return result

    def _get_root_pages_paths(self) -> list[Path]:
        """Return paths from `DIRS` plus optional `BASE_DIR` / `pages_dir`.

        Memoised per instance. A settings reload recreates the backend,
        so the memo never outlives its configuration.
        """
        if self._root_pages_paths_cache is not None:
            return self._root_pages_paths_cache
        result: list[Path] = [p.resolve() for p in self._extra_root_paths if p.exists()]
        if not self.app_dirs and not result:
            base_dir = resolve_base_dir()
            if base_dir is not None:
                pages_path = base_dir / self.pages_dir
                if pages_path.exists():
                    result.append(pages_path)
        self._root_pages_paths_cache = result
        return result

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
    """Build `RouterBackend` instances from `PAGE_BACKENDS`-style dicts."""

    _backends: ClassVar[dict[str, type[RouterBackend]]] = {
        "next.urls.FileRouterBackend": FileRouterBackend
    }

    @classmethod
    def register_backend(cls, name: str, backend_class: type[RouterBackend]) -> None:
        """Map a dotted backend path to a class for `create_backend`."""
        cls._backends[name] = backend_class

    @classmethod
    def create_backend(cls, config: dict[str, Any]) -> RouterBackend:
        """Instantiate the backend class named by `config["BACKEND"]`."""
        backend_name = config["BACKEND"]
        backend_class: Any

        if backend_name in cls._backends:
            backend_class = cls._backends[backend_name]
        else:
            try:
                backend_class = import_class_cached(backend_name)
            except ImportError as e:
                msg = f"Unsupported backend: {backend_name}"
                raise ValueError(msg) from e

        if not isinstance(backend_class, type) or not issubclass(
            backend_class, RouterBackend
        ):
            msg = f"Backend {backend_name!r} is not a RouterBackend subclass"
            raise TypeError(msg)

        if issubclass(backend_class, FileRouterBackend):
            for req in ("PAGES_DIR", "APP_DIRS", "OPTIONS", "DIRS"):
                if req not in config:
                    raise KeyError(req)
            base_dir = resolve_base_dir()
            raw_opts = config.get("OPTIONS")
            if not isinstance(raw_opts, dict):
                raw_opts = {}
            dirs_list = list(config.get("DIRS") or [])
            path_roots, segment_names = classify_dirs_entries(dirs_list, base_dir)
            components_dir = FileRouterBackend._resolve_components_folder_name()
            skip_names = frozenset({components_dir, *segment_names})
            narrow_opts = _narrow_file_router_options(raw_opts)
            return backend_class(
                pages_dir=config.get("PAGES_DIR", "pages"),
                app_dirs=bool(config.get("APP_DIRS", True)),
                extra_root_paths=path_roots,
                skip_dir_names=skip_names,
                components_folder_name=components_dir,
                options=narrow_opts,
            )
        return backend_class()


__all__ = ["FileRouterBackend", "RouterBackend", "RouterFactory"]
