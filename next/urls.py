"""File-based URL routing system for Django applications.

This module implements a sophisticated URL pattern generation system that automatically
creates Django URL patterns from page.py files located in application directories.
The system supports multiple routing strategies, configuration management, and provides
a plugin architecture for extensibility.

The system follows the Strategy pattern for backend selection and the Factory pattern
for object creation, ensuring high extensibility and maintainability.
"""

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Generator
from pathlib import Path
from typing import Any, ClassVar

from django.conf import settings
from django.urls import URLPattern, URLResolver

from .pages import page


logger = logging.getLogger(__name__)


# Configuration constants
DEFAULT_PAGES_DIR = "pages"
DEFAULT_APP_DIRS = True


class URLPatternParser:
    """Converts file-based URL paths to Django URL patterns with parameter extraction.

    This parser implements a custom URL syntax that maps file system structures
    to Django URL patterns. It supports parameterized routes with type hints
    and wildcard arguments, making it easy to create RESTful APIs from file
    structures.
    """

    def __init__(self) -> None:
        """Initialize URL pattern parser with regex patterns."""
        # regex patterns for different parameter types
        self._param_pattern = re.compile(r"\[([^\[\]]+)\]")  # [param] or [int:param]
        self._args_pattern = re.compile(r"\[\[([^\[\]]+)\]\]")  # [[args]]

    def parse_url_pattern(self, url_path: str) -> tuple[str, dict[str, str]]:
        """Convert file-based URL path to Django URL pattern with parameter extraction.

        Processes the URL path to identify parameterized segments and converts them
        to Django's URL pattern syntax. Handles both typed parameters and wildcard
        arguments, ensuring proper Django URL pattern generation.

        Returns a tuple of (django_pattern, parameters_dict) where the pattern
        can be used directly with Django's path() function and parameters contains
        the mapping of parameter names for view function context.
        """
        django_pattern = url_path
        parameters: dict[str, str] = {}

        # process regular arguments ([[args]])
        if args_match := self._args_pattern.search(django_pattern):
            args_name = args_match.group(1)
            django_args_name = args_name.replace("-", "_")
            django_pattern = self._args_pattern.sub(
                f"<path:{django_args_name}>",
                django_pattern,
            )
            parameters[django_args_name] = django_args_name

        # process regular parameters ([param] or [type:param])
        param_matches = self._param_pattern.findall(url_path)
        for param_str in param_matches:
            param_name, param_type = self._parse_param_name_and_type(param_str)
            django_param_name = param_name.replace("-", "_")

            # replace all occurrences of this parameter
            django_pattern = django_pattern.replace(
                f"[{param_str}]",
                f"<{param_type}:{django_param_name}>",
            )
            parameters[django_param_name] = django_param_name

        # ensure trailing slash for Django compatibility
        if django_pattern and not django_pattern.endswith("/"):
            django_pattern = f"{django_pattern}/"

        return django_pattern, parameters

    def _parse_param_name_and_type(self, param_str: str) -> tuple[str, str]:
        """Extract parameter name and type from parameter string.

        Parses parameter strings in the format "type:name" or just "name".
        Returns a tuple of (name, type) where type defaults to "str" if not specified.
        """
        if ":" in param_str:
            type_name, param_name = param_str.split(":", 1)
            return param_name.strip(), type_name.strip()
        return param_str.strip(), "str"

    def prepare_url_name(self, url_path: str) -> str:
        """Convert URL path to a valid Django URL pattern name.

        Sanitizes the URL path by replacing special characters with underscores
        and normalizing the resulting string to create a valid Python identifier
        suitable for use as a Django URL pattern name.
        """
        clean_name = url_path.replace("/", "_")
        # remove square brackets and replace with underscores
        clean_name = re.sub(r"[\[\]]", "_", clean_name)
        # remove colons from type:param syntax
        clean_name = clean_name.replace(":", "_")
        # replace hyphens with underscores
        clean_name = clean_name.replace("-", "_")
        # collapse multiple underscores into single underscore
        clean_name = re.sub(r"_+", "_", clean_name)
        # remove leading/trailing underscores
        return clean_name.strip("_")


class RouterBackend(ABC):
    """Abstract interface for URL pattern generation backends.

    Defines the contract that all routing backends must implement. This abstraction
    allows for different URL generation strategies (file-based, database-driven,
    API-based, etc.) to be used interchangeably through the same interface.

    The Strategy pattern implementation enables runtime selection of different
    routing approaches without modifying client code.
    """

    @abstractmethod
    def generate_urls(self) -> list[URLPattern | URLResolver]:
        """Generate URL patterns for this backend.

        Returns a list of Django URL patterns and resolvers that will be
        included in the main URL configuration. Each backend is responsible
        for implementing its own URL discovery and pattern generation logic.
        """


class FileRouterBackend(RouterBackend):
    """File-based URL pattern generation backend.

    Scans the file system for page.py files and generates Django URL patterns
    based on the directory structure. Supports both application-specific
    directories and root-level pages directories.
    """

    def __init__(
        self,
        pages_dir: str = DEFAULT_PAGES_DIR,
        *,
        app_dirs: bool = DEFAULT_APP_DIRS,
        options: dict[str, Any] | None = None,
    ) -> None:
        """Initialize router backend with configuration options."""
        self.pages_dir = pages_dir
        self.app_dirs = app_dirs
        self.options = options or {}
        self._patterns_cache: dict[str, list[URLPattern | URLResolver]] = {}
        self._url_parser = URLPatternParser()

    def __repr__(self) -> str:
        """Return string representation of the router backend."""
        return (
            f"<{self.__class__.__name__} pages_dir='{self.pages_dir}' "
            f"app_dirs={self.app_dirs}>"
        )

    def __eq__(self, other: object) -> bool:
        """Equality comparison."""
        if not isinstance(other, FileRouterBackend):
            return False
        return (
            self.pages_dir == other.pages_dir
            and self.app_dirs == other.app_dirs
            and self.options == other.options
        )

    def __hash__(self) -> int:
        """Hash for the router backend."""
        return hash(
            (self.pages_dir, self.app_dirs, tuple(sorted(self.options.items()))),
        )

    def generate_urls(self) -> list[URLPattern | URLResolver]:
        """Generate URL patterns based on backend configuration.

        Delegates to either app-specific or root-level URL generation
        based on the app_dirs configuration setting.
        """
        return (
            self._generate_app_urls() if self.app_dirs else self._generate_root_urls()
        )

    def _generate_app_urls(self) -> list[URLPattern | URLResolver]:
        """Generate URL patterns from Django application directories.

        Scans all installed Django applications for pages directories
        and generates URL patterns from each one. Patterns are added
        directly to the main URL configuration without wrapping.
        """
        urls: list[URLPattern | URLResolver] = []

        for app_name in self._get_installed_apps():
            if patterns := self._generate_urls_for_app(app_name):
                urls.extend(patterns)

        return urls

    def _generate_root_urls(self) -> list[URLPattern | URLResolver]:
        """Generate URL patterns from root-level pages directory.

        Scans the project root for a pages directory and generates
        URL patterns from page.py files found within it.
        """
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
            pages_path = app_path / self.pages_dir

            return pages_path if pages_path.exists() else None

        except (ImportError, AttributeError):
            return None

    def _get_root_pages_path(self) -> Path | None:
        """Get the root pages directory path."""
        if not (base_dir := getattr(settings, "BASE_DIR", None)):
            return None

        if isinstance(base_dir, str):
            base_dir = Path(base_dir)

        pages_path = base_dir / self.pages_dir
        return pages_path if pages_path.exists() else None

    def _generate_urls_for_app(self, app_name: str) -> list[URLPattern | URLResolver]:
        """Generate URL patterns for a specific Django application.

        Implements caching to avoid repeated directory scanning for the same
        application. Returns cached patterns if available, otherwise scans
        the application's pages directory and caches the results.
        """
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
        """Generate URL patterns from a pages directory.

        Scans the directory for page.py files and creates Django URL patterns
        for each one. Uses the Page class to handle template loading and
        view function creation.
        """
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
    ) -> Generator[tuple[str, Path], None, None]:
        """Recursively scan pages directory for page.py files and virtual views.

        Walks through the directory tree and identifies page.py files and
        directories with template.djx files (virtual views), constructing
        URL paths based on the directory structure. Each directory level
        becomes a URL segment, creating a hierarchical URL structure that
        mirrors the file system.
        """

        def scan_recursive(
            current_path: Path,
            url_path: str = "",
        ) -> Generator[tuple[str, Path], None, None]:
            for item in current_path.iterdir():
                if item.is_dir():
                    dir_name = item.name
                    new_url_path = f"{url_path}/{dir_name}" if url_path else dir_name
                    yield from scan_recursive(item, new_url_path)
                elif item.name == "page.py":
                    yield url_path, item

            # check for virtual views: directories with template.djx but no page.py
            if current_path.is_dir():
                page_file = current_path / "page.py"
                template_file = current_path / "template.djx"

                if not page_file.exists() and template_file.exists():
                    # create a virtual page.py path for the virtual view
                    virtual_page_path = current_path / "page.py"
                    yield url_path, virtual_page_path

        yield from scan_recursive(pages_path)


class RouterFactory:
    """Factory for creating router backend instances from configuration.

    Implements the Factory pattern to create appropriate backend instances
    based on configuration data. Supports dynamic backend registration
    and handles different backend types with their specific initialization
    requirements.

    The factory maintains a registry of available backends and can be
    extended at runtime by registering new backend types.
    """

    _backends: ClassVar[dict[str, type[RouterBackend]]] = {
        "next.urls.FileRouterBackend": FileRouterBackend,
    }

    @classmethod
    def register_backend(cls, name: str, backend_class: type[RouterBackend]) -> None:
        """Register a new router backend type.

        Allows dynamic registration of new backend types at runtime,
        enabling plugin-like extensibility for the routing system.
        """
        cls._backends[name] = backend_class

    @classmethod
    def create_backend(cls, config: dict[str, Any]) -> RouterBackend:
        """Create a router backend instance from configuration.

        Parses the configuration dictionary to determine the backend type
        and creates an appropriately configured instance. Handles different
        backend types with their specific initialization requirements.

        Raises ValueError if the specified backend type is not registered.
        """
        backend_name = config.get("BACKEND", "next.urls.FileRouterBackend")

        if backend_name not in cls._backends:
            msg = f"Unsupported backend: {backend_name}"
            raise ValueError(msg)

        backend_class = cls._backends[backend_name]

        # handle FileRouterBackend with specific configuration
        if issubclass(backend_class, FileRouterBackend):
            return backend_class(
                pages_dir=DEFAULT_PAGES_DIR,
                app_dirs=config.get("APP_DIRS", DEFAULT_APP_DIRS),
                options=config.get("OPTIONS", {}),
            )
        # for other backend types, create with default initialization
        return backend_class()


class RouterManager:
    """Centralized manager for multiple router backends and their configurations.

    Orchestrates multiple router backends, manages their lifecycle, and provides
    a unified interface for URL pattern generation. Implements lazy loading
    of configurations and provides caching for performance optimization.

    The manager follows the Facade pattern, providing a simple interface
    to the complex subsystem of router backends and configuration management.
    """

    def __init__(self) -> None:
        """Initialize router manager with empty router list."""
        self._routers: list[RouterBackend] = []
        self._config_cache: list[dict[str, Any]] | None = None

    def __repr__(self) -> str:
        """Return string representation of the router manager."""
        return f"<{self.__class__.__name__} routers={len(self._routers)}>"

    def __len__(self) -> int:
        """Return the number of configured routers."""
        return len(self._routers)

    def __iter__(self) -> Generator[URLPattern | URLResolver, None, None]:
        """Generate URLs from all configured routers.

        Implements lazy loading of router configurations and yields
        URL patterns from all registered backends in sequence.
        """
        if not self._routers:
            self._reload_config()

        for router in self._routers:
            yield from router.generate_urls()

    def __getitem__(self, index: int) -> RouterBackend:
        """Get router by index for direct access."""
        return self._routers[index]

    def _reload_config(self) -> None:
        """Reload router configurations from Django settings.

        Clears the current router cache and reloads configurations
        from Django settings. Handles configuration errors gracefully
        by logging them and continuing with other configurations.
        """
        self._config_cache = None
        self._routers.clear()

        configs = self._get_next_pages_config()
        for config in configs:
            try:
                if router := RouterFactory.create_backend(config):
                    self._routers.append(router)
            except Exception:
                logger.exception("error creating router from config %s", config)

    def _get_next_pages_config(self) -> list[dict[str, Any]]:
        """Get NEXT_PAGES configuration from Django settings.

        Retrieves the router configuration from Django settings with
        caching to avoid repeated settings access. Returns a default
        configuration if NEXT_PAGES is not defined in settings.
        """
        if self._config_cache is not None:
            return self._config_cache

        default_config = [
            {
                "BACKEND": "next.urls.FileRouterBackend",
                "APP_DIRS": DEFAULT_APP_DIRS,
                "OPTIONS": {},
            },
        ]

        self._config_cache = getattr(settings, "NEXT_PAGES", default_config)
        return self._config_cache


# global router manager instance for application-wide URL pattern management
router_manager = RouterManager()

# django URL configuration
app_name = "next"
urlpatterns = list(router_manager)
