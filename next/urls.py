"""
File-based router for Django applications.

Automatically generates URL patterns from page.py files in pages/ directories
of Django applications with Django-style configuration support.
"""

import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Generator, Iterator
from pathlib import Path
from typing import Any, cast

from django.conf import settings
from django.urls import URLPattern, URLResolver, include, path


class RouterBackend(ABC):
    """Abstract base class for router backends."""

    @abstractmethod
    def generate_urls(self) -> list[URLPattern | URLResolver]:
        """Generate URL patterns for this backend."""
        pass


class FileRouterBackend(RouterBackend):
    """File-based router backend implementation."""

    def __init__(
        self,
        pages_dir_name: str = "pages",
        app_dirs: bool = True,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.pages_dir_name = pages_dir_name
        self.app_dirs = app_dirs
        self.options = options or {}
        self._patterns_cache: dict[str, list[URLPattern | URLResolver]] = {}

        # url pattern parsing regexes
        self._param_pattern = re.compile(r"\[([^\[\]]+)\]")  # [param] or [int:param]
        self._args_pattern = re.compile(r"\[\[([^\[\]]+)\]\]")  # [[args]]

    def __repr__(self) -> str:
        """String representation of the router backend."""
        return f"<{self.__class__.__name__} pages_dir='{self.pages_dir_name}' app_dirs={self.app_dirs}>"

    def __eq__(self, other: Any) -> bool:
        """Equality comparison."""
        if not isinstance(other, FileRouterBackend):
            return False
        return (
            self.pages_dir_name == other.pages_dir_name
            and self.app_dirs == other.app_dirs
            and self.options == other.options
        )

    def __hash__(self) -> int:
        """Hash for the router backend."""
        return hash(
            (self.pages_dir_name, self.app_dirs, tuple(sorted(self.options.items())))
        )

    def generate_urls(self) -> list[URLPattern | URLResolver]:
        """Generate URL patterns based on configuration."""
        return (
            self._generate_app_urls() if self.app_dirs else self._generate_root_urls()
        )

    def _generate_app_urls(self) -> list[URLPattern | URLResolver]:
        """Generate URLs for Django apps."""
        urls: list[URLPattern | URLResolver] = []

        for app_name in self._get_installed_apps():
            if patterns := self._generate_urls_for_app(app_name):
                urls.append(self._create_app_include(app_name, patterns))

        return urls

    def _generate_root_urls(self) -> list[URLPattern | URLResolver]:
        """Generate URLs for root pages directory."""
        if pages_path := self._get_root_pages_path():
            return list(self._generate_patterns_from_directory(pages_path))
        return []

    def _get_installed_apps(self) -> Generator[str, None, None]:
        """Get installed Django apps, excluding Django built-ins."""
        for app in getattr(settings, "INSTALLED_APPS", []):
            if not app.startswith("django."):
                yield app

    def _get_app_pages_path(self, app_name: str) -> Path | None:
        """Get the pages directory path for a Django app."""
        try:
            app_module = __import__(app_name, fromlist=[""])
            if app_module.__file__ is None:
                return None

            app_path = Path(app_module.__file__).parent
            pages_path = app_path / self.pages_dir_name

            return pages_path if pages_path.exists() else None

        except (ImportError, AttributeError):
            return None

    def _get_root_pages_path(self) -> Path | None:
        """Get the root pages directory path."""
        if not (base_dir := getattr(settings, "BASE_DIR", None)):
            return None

        if isinstance(base_dir, str):
            base_dir = Path(base_dir)

        pages_path = base_dir / self.pages_dir_name
        return pages_path if pages_path.exists() else None

    def _generate_urls_for_app(self, app_name: str) -> list[URLPattern | URLResolver]:
        """Generate URL patterns for a specific Django app."""
        if app_name in self._patterns_cache:
            return self._patterns_cache[app_name]

        if pages_path := self._get_app_pages_path(app_name):
            patterns: list[URLPattern | URLResolver] = list(
                self._generate_patterns_from_directory(pages_path)
            )
            self._patterns_cache[app_name] = patterns
            return patterns
        return []

    def _generate_patterns_from_directory(
        self, pages_path: Path
    ) -> Generator[URLPattern, None, None]:
        """Generate URL patterns from a pages directory."""
        for url_path, file_path in self._scan_pages_directory(pages_path):
            if pattern := self._create_url_pattern(url_path, file_path):
                yield pattern

    def _scan_pages_directory(
        self, pages_path: Path
    ) -> Generator[tuple[str, Path], None, None]:
        """Scan pages directory for page.py files."""

        def scan_recursive(
            current_path: Path, url_path: str = ""
        ) -> Generator[tuple[str, Path], None, None]:
            for item in current_path.iterdir():
                if item.is_dir():
                    dir_name = item.name
                    new_url_path = f"{url_path}/{dir_name}" if url_path else dir_name
                    yield from scan_recursive(item, new_url_path)
                elif item.name == "page.py":
                    yield url_path, item

        yield from scan_recursive(pages_path)

    def _create_url_pattern(self, url_path: str, file_path: Path) -> URLPattern | None:
        """Create Django URL pattern from page.py file."""
        django_pattern, parameters = self._parse_url_pattern(url_path)
        if not (render_func := self._load_page_function(file_path)):
            return None

        def view_wrapper(request: Any, **kwargs: Any) -> Any:
            """Wrapper to handle parameter passing to render function."""
            if "args" in parameters and "args" in kwargs:
                kwargs["args"] = kwargs["args"].split("/")
            return render_func(request, **kwargs)

        return path(
            django_pattern, view_wrapper, name=f"page_{url_path.replace('/', '_')}"
        )

    def _parse_url_pattern(self, url_path: str) -> tuple[str, dict[str, str]]:
        """Parse URL path and extract parameters."""
        django_pattern = url_path
        parameters: dict[str, str] = {}

        # handle [[args]] pattern first
        if args_match := self._args_pattern.search(django_pattern):
            args_name = args_match.group(1)
            django_args_name = args_name.replace("-", "_")
            django_pattern = self._args_pattern.sub(
                f"<path:{django_args_name}>", django_pattern
            )
            parameters[django_args_name] = django_args_name

        # handle [param] pattern after
        param_matches = self._param_pattern.findall(url_path)
        for param_str in param_matches:
            param_name, param_type = self._parse_param_name_and_type(param_str)
            django_param_name = param_name.replace("-", "_")

            # replace only the first occurrence of this specific pattern
            django_pattern = django_pattern.replace(
                f"[{param_str}]", f"<{param_type}:{django_param_name}>", 1
            )
            parameters[django_param_name] = django_param_name

        return django_pattern, parameters

    def _parse_param_name_and_type(self, param_str: str) -> tuple[str, str]:
        """Parse parameter string to extract name and type."""
        if ":" in param_str:
            type_name, param_name = param_str.split(":", 1)
            return param_name.strip(), type_name.strip()
        return param_str.strip(), "str"

    def _load_page_function(self, file_path: Path) -> Callable[..., Any] | None:
        """Load render function from page.py file."""
        try:
            import importlib.util

            if (
                spec := importlib.util.spec_from_file_location("page_module", file_path)
            ) is None or spec.loader is None:
                return None

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if render_func := getattr(module, "render", None):
                return render_func if callable(render_func) else None
            return None

        except Exception as e:
            print(f"Error loading page function from {file_path}: {e}")
            return None

    def _create_app_include(
        self, app_name: str, patterns: list[URLPattern | URLResolver]
    ) -> URLResolver:
        """Create include() for app pages."""
        return cast(
            URLResolver, include((patterns, app_name), namespace=f"{app_name}_pages")
        )


class RouterFactory:
    """Factory for creating router backends from configuration."""

    _backends: dict[str, type[RouterBackend]] = {
        "next.urls.FileRouterBackend": FileRouterBackend,
    }

    @classmethod
    def register_backend(cls, name: str, backend_class: type[RouterBackend]) -> None:
        """Register a new router backend."""
        cls._backends[name] = backend_class

    @classmethod
    def create_backend(cls, config: dict[str, Any]) -> RouterBackend:
        """Create router backend from configuration."""
        backend_name = config.get("BACKEND", "next.urls.FileRouterBackend")

        if backend_name not in cls._backends:
            raise ValueError(f"Unsupported backend: {backend_name}")

        backend_class = cls._backends[backend_name]
        # type check to ensure we're working with FileRouterBackend
        if issubclass(backend_class, FileRouterBackend):
            return backend_class(
                pages_dir_name=config.get("OPTIONS", {}).get("PAGES_DIR_NAME", "pages"),
                app_dirs=config.get("APP_DIRS", True),
                options=config.get("OPTIONS", {}),
            )
        else:
            # for other backend types, create without specific arguments
            return backend_class()


class RouterManager:
    """Manages multiple router backends and their configurations."""

    def __init__(self) -> None:
        self._routers: list[RouterBackend] = []
        self._config_cache: list[dict[str, Any]] | None = None

    def __repr__(self) -> str:
        """String representation of the router manager."""
        return f"<{self.__class__.__name__} routers={len(self._routers)}>"

    def __len__(self) -> int:
        """Number of configured routers."""
        return len(self._routers)

    def __iter__(self) -> Iterator[RouterBackend]:
        """Iterate over configured routers."""
        return iter(self._routers)

    def __getitem__(self, index: int) -> RouterBackend:
        """Get router by index."""
        return self._routers[index]

    def reload_config(self) -> None:
        """Reload configuration from Django settings."""
        self._config_cache = None
        self._routers.clear()

        configs = self._get_next_pages_config()
        for config in configs:
            try:
                if router := RouterFactory.create_backend(config):
                    self._routers.append(router)
            except Exception as e:
                print(f"Error creating router from config {config}: {e}")

    def _get_next_pages_config(self) -> list[dict[str, Any]]:
        """Get NEXT_PAGES configuration from Django settings."""
        if self._config_cache is not None:
            return self._config_cache

        default_config = [
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "APP_DIRS": True,
                "OPTIONS": {},
            }
        ]

        self._config_cache = getattr(settings, "NEXT_PAGES", default_config)
        return self._config_cache

    def generate_all_urls(self) -> list[URLPattern | URLResolver]:
        """Generate URLs from all configured routers."""
        if not self._routers:
            self.reload_config()

        urls: list[URLPattern | URLResolver] = []
        for router in self._routers:
            urls.extend(router.generate_urls())

        return urls


# global router manager instance
router_manager = RouterManager()


# public API functions
def get_next_pages_config() -> list[dict[str, Any]]:
    """Get NEXT_PAGES configuration from Django settings."""
    return router_manager._get_next_pages_config()


def create_router_from_config(config: dict[str, Any]) -> RouterBackend:
    """Create router from configuration dictionary."""
    return RouterFactory.create_backend(config)


def get_configured_routers() -> list[RouterBackend]:
    """Get list of configured routers from NEXT_PAGES setting."""
    if not router_manager._routers:
        router_manager.reload_config()
    return list(router_manager._routers)


def include_pages(app_name: str) -> URLResolver:
    """Include pages for a Django app."""
    # create a temporary router for this specific app
    router = FileRouterBackend(app_dirs=True)

    if patterns := router._generate_urls_for_app(app_name):
        return router._create_app_include(app_name, patterns)

    # return empty include with proper namespace
    return cast(URLResolver, include(([], app_name), namespace=f"{app_name}_pages"))


def auto_include_all_pages() -> list[URLPattern | URLResolver]:
    """Automatically include pages for all configured routers."""
    return router_manager.generate_all_urls()


# empty urlpatterns - will be generated on demand
urlpatterns: list[URLPattern | URLResolver] = []
