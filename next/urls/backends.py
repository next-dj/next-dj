"""Pluggable router backend contract, file router, and backend factory.

The `RouterBackend` ABC defines the contract every router
implementation must satisfy. `FileRouterBackend` is the built-in
implementation that discovers `page.py` (and virtual `template.djx`)
entries under app and optional root page trees. `RouterFactory` maps
dotted backend paths to classes and instantiates them from
`DEFAULT_PAGE_BACKENDS` config dicts.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from django.conf import settings

from next.conf import import_class_cached, next_framework_settings
from next.pages import page
from next.utils import classify_dirs_entries, resolve_base_dir

from .dispatcher import FilesystemTreeDispatcher
from .parser import default_url_parser


if TYPE_CHECKING:
    from collections.abc import Generator

    from django.urls import URLPattern, URLResolver


logger = logging.getLogger(__name__)


class RouterBackend(ABC):
    """Pluggable source of `URLPattern` and `URLResolver` entries."""

    @abstractmethod
    def generate_urls(self) -> list[URLPattern | URLResolver]:
        """Patterns contributed by this backend to the project URLconf."""


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

    def __init__(  # noqa: PLR0913
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
        self._app_pages_path_cache: dict[str, Path | None] = {}
        self._url_parser = default_url_parser

    @staticmethod
    def _resolve_components_folder_name() -> str:
        """Folder name to skip in URL scans.

        Taken from the first `DEFAULT_COMPONENT_BACKENDS` entry.
        """
        cbs = next_framework_settings.DEFAULT_COMPONENT_BACKENDS
        _components_key = "COMPONENTS_DIR"
        if not isinstance(cbs, list) or not cbs:
            raise KeyError(_components_key)
        cb0 = cbs[0]
        if not isinstance(cb0, dict) or _components_key not in cb0:
            raise KeyError(_components_key)
        return str(cb0[_components_key])

    def __repr__(self) -> str:
        """Debug representation."""
        return (
            f"<{self.__class__.__name__} pages_dir='{self.pages_dir}' "
            f"app_dirs={self.app_dirs}>"
        )

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
            ),
        )

    def generate_urls(self) -> list[URLPattern | URLResolver]:
        """Yield app routes first when `app_dirs` is set, then root `pages` dirs."""
        if self.app_dirs:
            urls = self._generate_app_urls()
            urls.extend(self._generate_root_urls())
            return urls
        return self._generate_root_urls()

    def _generate_app_urls(self) -> list[URLPattern | URLResolver]:
        """Return patterns from each installed app's `pages_dir` tree."""
        urls: list[URLPattern | URLResolver] = []
        for app_name in self._get_installed_apps():
            if patterns := self._generate_urls_for_app(app_name):
                urls.extend(patterns)
        return urls

    def _generate_root_urls(self) -> list[URLPattern | URLResolver]:
        """Patterns from each configured root pages directory."""
        urls: list[URLPattern | URLResolver] = []
        for pages_path in self._get_root_pages_paths():
            urls.extend(self._generate_patterns_from_directory(pages_path))
        return urls

    def _get_installed_apps(self) -> Generator[str, None, None]:
        """Yield installed app names except django framework packages."""
        for app in getattr(settings, "INSTALLED_APPS", []):
            if not app.startswith("django."):
                yield app

    def _get_app_pages_path(self, app_name: str) -> Path | None:
        """Return `<app>/pages_dir` when that directory exists."""
        if app_name in self._app_pages_path_cache:
            return self._app_pages_path_cache[app_name]
        try:
            app_module = __import__(app_name, fromlist=[""])
            if app_module.__file__ is None:
                self._app_pages_path_cache[app_name] = None
                return None
            app_path = Path(app_module.__file__).parent
            pages_path = app_path / self.pages_dir
            result = pages_path if pages_path.exists() else None
        except (ImportError, AttributeError):
            result = None
        self._app_pages_path_cache[app_name] = result
        return result

    def _get_root_pages_paths(self) -> list[Path]:
        """Return paths from `DIRS` plus optional `BASE_DIR` / `pages_dir`."""
        result: list[Path] = [p.resolve() for p in self._extra_root_paths if p.exists()]
        if not self.app_dirs and not result:
            base_dir = resolve_base_dir()
            if base_dir is not None:
                pages_path = base_dir / self.pages_dir
                if pages_path.exists():
                    result.append(pages_path)
        return result

    def _generate_urls_for_app(self, app_name: str) -> list[URLPattern | URLResolver]:
        """Return cached patterns for one app, scanning on first use."""
        if app_name in self._patterns_cache:
            return self._patterns_cache[app_name]
        if pages_path := self._get_app_pages_path(app_name):
            patterns: list[URLPattern | URLResolver] = list(
                self._generate_patterns_from_directory(pages_path),
            )
            self._patterns_cache[app_name] = patterns
            return patterns
        return []

    def _generate_patterns_from_directory(
        self,
        pages_path: Path,
    ) -> Generator[URLPattern, None, None]:
        """Yield one `URLPattern` per discovered page under `pages_path`."""
        for url_path, file_path in self._scan_pages_directory(pages_path):
            if pattern := page.create_url_pattern(
                url_path,
                file_path,
                self._url_parser,
            ):
                yield pattern

    def _scan_pages_directory(
        self,
        pages_path: Path,
        *,
        register_components: bool = True,
    ) -> Generator[tuple[str, Path], None, None]:
        """Yield `(url_path, page_file)` pairs discovered under `pages_path`."""
        dispatcher = FilesystemTreeDispatcher(
            self._skip_dir_names,
            components_folder_name=self._components_folder_name,
            register_components=register_components,
        )
        yield from dispatcher.walk(pages_path)


class RouterFactory:
    """Build `RouterBackend` instances from `DEFAULT_PAGE_BACKENDS`-style dicts."""

    _backends: ClassVar[dict[str, type[RouterBackend]]] = {
        "next.urls.FileRouterBackend": FileRouterBackend,
    }

    @classmethod
    def register_backend(cls, name: str, backend_class: type[RouterBackend]) -> None:
        """Map a dotted backend path to a class for `create_backend`."""
        cls._backends[name] = backend_class

    @classmethod
    def is_filesystem_discovery_router_class(cls, router_class: object) -> bool:
        """Return True if `router_class` implements the filesystem page-tree API."""
        if not isinstance(router_class, type):
            return False
        if issubclass(router_class, FileRouterBackend):
            return True
        if not issubclass(router_class, RouterBackend):
            return False
        required = (
            "generate_urls",
            "_get_installed_apps",
            "_get_app_pages_path",
            "_get_root_pages_paths",
        )
        return all(hasattr(router_class, name) for name in required)

    @classmethod
    def is_filesystem_discovery_router(cls, obj: object) -> bool:
        """Whether `obj` is a router instance that walks pages trees (duck typing)."""
        if obj is None:
            return False
        return (
            hasattr(obj, "pages_dir")
            and hasattr(obj, "app_dirs")
            and hasattr(obj, "options")
            and hasattr(obj, "generate_urls")
            and callable(getattr(obj, "generate_urls", None))
            and hasattr(obj, "_get_installed_apps")
            and callable(getattr(obj, "_get_installed_apps", None))
            and hasattr(obj, "_get_app_pages_path")
            and callable(getattr(obj, "_get_app_pages_path", None))
            and hasattr(obj, "_get_root_pages_paths")
            and callable(getattr(obj, "_get_root_pages_paths", None))
            and hasattr(obj, "_skip_dir_names")
        )

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
            backend_class,
            RouterBackend,
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


__all__ = [
    "FileRouterBackend",
    "RouterBackend",
    "RouterFactory",
]
