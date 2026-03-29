"""Build Django URL patterns from filesystem routes under configured ``pages/`` trees.

Router backends come from merged ``NEXT_FRAMEWORK`` (see ``DEFAULT_PAGE_ROUTERS``).
The default backend walks ``page.py`` and virtual ``template.djx`` entries and turns
paths and bracket segments into ``URLPattern`` objects.
"""

import inspect
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Generator, Iterable
from pathlib import Path
from typing import Any, ClassVar, TypeVar, get_args, get_origin

from django.conf import settings
from django.http import HttpRequest
from django.urls import URLPattern, URLResolver

from .conf import import_class_cached, next_framework_settings
from .deps import DDependencyBase, RegisteredParameterProvider
from .forms import form_action_manager
from .pages import page


logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class DUrl(DDependencyBase[_T]):
    """Annotation for a path or query parameter with optional type coercion.

    Use ``DUrl["param"]`` or ``DUrl[SomeType]``.
    """

    __slots__ = ()


def _coerce_url_value(value: str, hint: type) -> object:
    """Coerce a URL string toward ``int``, ``bool``, ``float``, or ``str``.

    Leave the original string when conversion fails.
    """
    if hint is int:
        try:
            return int(value)
        except ValueError:
            return value
    if hint is bool:
        return value.lower() in ("1", "true", "yes")
    if hint is float:
        try:
            return float(value)
        except ValueError:
            return value
    return value


class HttpRequestProvider(RegisteredParameterProvider):
    """Supplies ``HttpRequest`` from ``context.request``."""

    def can_handle(self, param: inspect.Parameter, context: object) -> bool:
        """Return whether the parameter is ``HttpRequest`` and a request exists."""
        if getattr(context, "request", None) is None:
            return False
        origin = get_origin(param.annotation)
        return origin is None and param.annotation is HttpRequest

    def resolve(self, _param: inspect.Parameter, context: object) -> object:
        """Return the request from the resolution context."""
        return getattr(context, "request", None)


class UrlByAnnotationProvider(RegisteredParameterProvider):
    """Fills ``DUrl[...]`` parameters from ``url_kwargs``."""

    def can_handle(self, param: inspect.Parameter, _context: object) -> bool:
        """Whether the parameter uses a ``DUrl`` annotation."""
        return get_origin(param.annotation) is DUrl

    def resolve(self, param: inspect.Parameter, context: object) -> object:
        """URL value for the parameter, coerced when the annotation is a type."""
        args = get_args(param.annotation)
        key = args[0] if args and isinstance(args[0], str) else param.name
        url_kwargs = getattr(context, "url_kwargs", {}) or {}
        raw = (
            url_kwargs.get(key) if isinstance(key, str) else url_kwargs.get(param.name)
        )
        if raw is None:
            return None
        hint = args[0] if args and isinstance(args[0], type) else str
        return _coerce_url_value(str(raw), hint)


class UrlKwargsProvider(RegisteredParameterProvider):
    """Fills parameters by name from ``url_kwargs``."""

    def can_handle(self, param: inspect.Parameter, context: object) -> bool:
        """Whether ``url_kwargs`` contains this parameter name."""
        return param.name in (getattr(context, "url_kwargs", {}) or {})

    def resolve(self, param: inspect.Parameter, context: object) -> object:
        """Raw URL value for the parameter, coerced to the annotation when possible."""
        url_kwargs = getattr(context, "url_kwargs", {}) or {}
        raw = url_kwargs.get(param.name)
        if raw is None:
            return None
        hint = (
            param.annotation if param.annotation is not inspect.Parameter.empty else str
        )
        if hint is str or hint is inspect.Parameter.empty:
            return str(raw)
        return _coerce_url_value(str(raw), hint)


class URLPatternParser:
    """Maps bracket segments in a file-based path to Django path converters."""

    def __init__(self) -> None:
        """Compile matchers for ``[name]``, ``[type:name]``, and ``[[args]]``."""
        # regex patterns for different parameter types
        self._param_pattern = re.compile(r"\[([^\[\]]+)\]")  # [param] or [int:param]
        self._args_pattern = re.compile(r"\[\[([^\[\]]+)\]\]")  # [[args]]

    def parse_url_pattern(self, url_path: str) -> tuple[str, dict[str, str]]:
        """Django path string and parameter names derived from ``url_path``."""
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
        """Split bracket text into a name and converter label (default ``str``)."""
        if ":" in param_str:
            type_name, param_name = param_str.split(":", 1)
            return param_name.strip(), type_name.strip()
        return param_str.strip(), "str"

    def prepare_url_name(self, url_path: str) -> str:
        """Python-safe name for ``reverse`` from a filesystem-style ``url_path``."""
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
    """Pluggable source of ``URLPattern`` / ``URLResolver`` entries."""

    @abstractmethod
    def generate_urls(self) -> list[URLPattern | URLResolver]:
        """Patterns contributed by this backend to the project URLconf."""


class FileRouterBackend(RouterBackend):
    """Discovers ``page.py`` (and virtual pages) under app and optional root trees."""

    def __init__(
        self,
        pages_dir: str | None = None,
        *,
        app_dirs: bool | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        """Configure pages subdirectory, whether to scan installed apps, and extras."""
        self.pages_dir = pages_dir if pages_dir is not None else "pages"
        self.app_dirs = app_dirs if app_dirs is not None else True
        self.options = options or {}
        self._patterns_cache: dict[str, list[URLPattern | URLResolver]] = {}
        self._url_parser = URLPatternParser()

    def __repr__(self) -> str:
        """Debug representation."""
        return (
            f"<{self.__class__.__name__} pages_dir='{self.pages_dir}' "
            f"app_dirs={self.app_dirs}>"
        )

    def __eq__(self, other: object) -> bool:
        """Return whether ``other`` has the same pages config as this backend."""
        if not isinstance(other, FileRouterBackend):
            return False
        return (
            self.pages_dir == other.pages_dir
            and self.app_dirs == other.app_dirs
            and self.options == other.options
        )

    def __hash__(self) -> int:
        """Hash from ``pages_dir``, ``app_dirs``, and ``options``."""
        return hash(
            (self.pages_dir, self.app_dirs, tuple(sorted(self.options.items()))),
        )

    def generate_urls(self) -> list[URLPattern | URLResolver]:
        """Yield app routes first when ``app_dirs``, then root ``PAGES_*`` dirs."""
        if self.app_dirs:
            urls = self._generate_app_urls()
            urls.extend(self._generate_root_urls())
            return urls
        return self._generate_root_urls()

    def _generate_app_urls(self) -> list[URLPattern | URLResolver]:
        """Patterns from each installed app's ``pages_dir`` tree."""
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
        """Non-``django.*`` entries from ``INSTALLED_APPS``."""
        for app in getattr(settings, "INSTALLED_APPS", []):
            if not app.startswith("django."):
                yield app

    def _get_app_pages_path(self, app_name: str) -> Path | None:
        """``<app>/pages_dir`` when that directory exists."""
        try:
            app_module = __import__(app_name, fromlist=[""])
            if app_module.__file__ is None:
                return None

            app_path = Path(app_module.__file__).parent
            pages_path = app_path / self.pages_dir

            return pages_path if pages_path.exists() else None

        except (ImportError, AttributeError):
            return None

    def _get_root_pages_paths(self) -> list[Path]:
        """List existing dirs from ``PAGES_DIRS``, ``PAGES_DIR``, or ``BASE_DIR``."""
        result: list[Path] = []
        options = self.options

        if "PAGES_DIRS" in options:
            dirs = options["PAGES_DIRS"]
            if isinstance(dirs, (list, tuple)):
                for item in dirs:
                    path = Path(item) if not isinstance(item, Path) else item
                    if path.exists():
                        result.append(path)
            return result

        if "PAGES_DIR" in options:
            path = (
                Path(options["PAGES_DIR"])
                if not isinstance(options["PAGES_DIR"], Path)
                else options["PAGES_DIR"]
            )
            if path.exists():
                result.append(path)
            return result

        if not self.app_dirs and (base_dir := getattr(settings, "BASE_DIR", None)):
            if isinstance(base_dir, str):
                base_dir = Path(base_dir)
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
        """One ``URLPattern`` per discovered page under ``pages_path``."""
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
        """``(url_path, page.py path)`` for real and virtual pages."""
        skip_dir_names = (self.options.get("COMPONENTS_DIR", "_components"),)
        yield from _scan_pages_directory(pages_path, skip_dir_names)


def _scan_pages_directory(
    pages_path: Path,
    skip_dir_names: Iterable[str] = (),
) -> Generator[tuple[str, Path], None, None]:
    """Scan a pages directory for page.py and virtual views (template.djx only).

    Directories whose names are in skip_dir_names are not recursed into and
    do not create URL segments (e.g. component folder from OPTIONS.COMPONENTS_DIR).
    """
    skip_set = frozenset(skip_dir_names)

    def scan_recursive(
        current_path: Path,
        url_path: str = "",
    ) -> Generator[tuple[str, Path], None, None]:
        try:
            items = list(current_path.iterdir())
        except OSError as e:
            logger.debug("Cannot list directory %s: %s", current_path, e)
            return
        for item in items:
            if item.is_dir():
                if item.name in skip_set:
                    continue
                dir_name = item.name
                new_url_path = f"{url_path}/{dir_name}" if url_path else dir_name
                yield from scan_recursive(item, new_url_path)
            elif item.name == "page.py":
                yield url_path, item

        if current_path.is_dir():
            page_file = current_path / "page.py"
            template_file = current_path / "template.djx"
            if not page_file.exists() and template_file.exists():
                yield url_path, current_path / "page.py"

    yield from scan_recursive(pages_path)


class RouterFactory:
    """Builds ``RouterBackend`` instances from ``DEFAULT_PAGE_ROUTERS``-style dicts."""

    _backends: ClassVar[dict[str, type[RouterBackend]]] = {
        "next.urls.FileRouterBackend": FileRouterBackend,
    }

    @classmethod
    def register_backend(cls, name: str, backend_class: type[RouterBackend]) -> None:
        """Map a dotted backend path to a class for ``create_backend``."""
        cls._backends[name] = backend_class

    @classmethod
    def create_backend(cls, config: dict[str, Any]) -> RouterBackend:
        """Instantiate the backend class named by ``config["BACKEND"]``."""
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

        # handle FileRouterBackend with specific configuration
        if issubclass(backend_class, FileRouterBackend):
            return backend_class(
                pages_dir=config["PAGES_DIR"],
                app_dirs=config["APP_DIRS"],
                options=config["OPTIONS"],
            )
        # for other backend types, create with default initialization
        return backend_class()


class RouterManager:
    """Load ``RouterBackend`` instances from ``NEXT_FRAMEWORK`` and iterate them."""

    def __init__(self) -> None:
        """Empty backend list until first iteration."""
        self._routers: list[RouterBackend] = []
        self._config_cache: list[dict[str, Any]] | None = None

    def __repr__(self) -> str:
        """Debug representation with backend count."""
        return f"<{self.__class__.__name__} routers={len(self._routers)}>"

    def __len__(self) -> int:
        """Return the number of configured backends."""
        return len(self._routers)

    def __iter__(self) -> Generator[URLPattern | URLResolver, None, None]:
        """All patterns from each backend, loading config on first use."""
        if not self._routers:
            self._reload_config()

        for router in self._routers:
            yield from router.generate_urls()

    def __getitem__(self, index: int) -> RouterBackend:
        """Backend at ``index``."""
        return self._routers[index]

    def _reload_config(self) -> None:
        """Reload backends from ``DEFAULT_PAGE_ROUTERS``."""
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
        """ROUTERS from ``settings.NEXT_FRAMEWORK`` (merged with defaults, cached)."""
        if self._config_cache is not None:
            return self._config_cache

        routers = next_framework_settings.DEFAULT_PAGE_ROUTERS
        if not isinstance(routers, list):
            self._config_cache = []
            return self._config_cache

        self._config_cache = routers
        return self._config_cache


# global router manager instance for application-wide URL pattern management
router_manager = RouterManager()

# django URL configuration
app_name = "next"
urlpatterns = [*list(router_manager), *list(form_action_manager)]
