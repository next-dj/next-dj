"""Build Django URL patterns from filesystem routes under configured page trees.

Router backends come from merged ``NEXT_FRAMEWORK`` and ``DEFAULT_PAGE_BACKENDS``.
``FilesystemTreeDispatcher`` walks the tree depth first for URL patterns and passes
component folders to ``next.components``. The default backend discovers ``page.py``
and virtual ``template.djx`` entries and turns path segments into ``URLPattern``
objects.
"""

import inspect
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Generator, Iterable, Iterator
from pathlib import Path
from typing import Any, ClassVar, SupportsIndex, TypeVar, get_args, get_origin, overload

from django.conf import settings
from django.http import HttpRequest
from django.urls import URLPattern, URLResolver

from .conf import import_class_cached, next_framework_settings
from .deps import DDependencyBase, RegisteredParameterProvider
from .forms import form_action_manager
from .pages import page
from .utils import classify_dirs_entries, resolve_base_dir


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
    """Supply ``HttpRequest`` from ``context.request``."""

    def can_handle(self, param: inspect.Parameter, context: object) -> bool:
        """Return True when the parameter is ``HttpRequest`` and a request exists."""
        if getattr(context, "request", None) is None:
            return False
        origin = get_origin(param.annotation)
        return origin is None and param.annotation is HttpRequest

    def resolve(self, _param: inspect.Parameter, context: object) -> object:
        """Return the request from the resolution context."""
        return getattr(context, "request", None)


class UrlByAnnotationProvider(RegisteredParameterProvider):
    """Fill ``DUrl[...]`` parameters from ``url_kwargs``."""

    def can_handle(self, param: inspect.Parameter, _context: object) -> bool:
        """Return True when the parameter uses a ``DUrl`` annotation."""
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
    """Fill parameters by name from ``url_kwargs``."""

    def can_handle(self, param: inspect.Parameter, context: object) -> bool:
        """Return True when ``url_kwargs`` contains this parameter name."""
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
    """Map bracket segments in a file-based path to Django path converters.

    The ``url_path`` string is the logical URL trail built from directory names. An
    empty string means the tree root. It is not a ``pathlib.Path``. The on-disk file
    is the second value from the page-tree scanner.
    """

    def __init__(self) -> None:
        """Compile matchers for ``[name]``, ``[type:name]``, and ``[[args]]``."""
        # regex patterns for different parameter types
        self._param_pattern = re.compile(r"\[([^\[\]]+)\]")  # [param] or [int:param]
        self._args_pattern = re.compile(r"\[\[([^\[\]]+)\]\]")  # [[args]]

    def parse_url_pattern(self, url_path: str) -> tuple[str, dict[str, str]]:
        """Return the Django path string and parameter names for ``url_path``."""
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
    """Pluggable source of ``URLPattern`` and ``URLResolver`` entries."""

    @abstractmethod
    def generate_urls(self) -> list[URLPattern | URLResolver]:
        """Patterns contributed by this backend to the project URLconf."""


def _narrow_file_router_options(options: dict[str, Any]) -> dict[str, Any]:
    """Keep only keys consumed by ``next.pages`` (e.g. ``context_processors``)."""
    cp = options.get("context_processors")
    if not isinstance(cp, list):
        cp = []
    cp = [x for x in cp if isinstance(x, str)]
    if not cp:
        return {}
    return {"context_processors": cp}


class FileRouterBackend(RouterBackend):
    """Discover ``page.py`` (and virtual pages) under app and optional root trees."""

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
        self._url_parser = URLPatternParser()

    @staticmethod
    def _resolve_components_folder_name() -> str:
        """Folder name to skip in URL scans.

        Taken from the first ``DEFAULT_COMPONENT_BACKENDS`` entry.
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
        """Yield app routes first when ``app_dirs`` is set, then root ``pages`` dirs."""
        if self.app_dirs:
            urls = self._generate_app_urls()
            urls.extend(self._generate_root_urls())
            return urls
        return self._generate_root_urls()

    def _generate_app_urls(self) -> list[URLPattern | URLResolver]:
        """Return patterns from each installed app's ``pages_dir`` tree."""
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
        """Return ``<app>/pages_dir`` when that directory exists."""
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
        """Return paths from ``DIRS`` plus optional ``BASE_DIR`` / ``pages_dir``."""
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
        """Yield one ``URLPattern`` per discovered page under ``pages_path``."""
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
        """Yield ``(url_path, page_file)`` pairs discovered under ``pages_path``."""
        dispatcher = FilesystemTreeDispatcher(
            self._skip_dir_names,
            components_folder_name=self._components_folder_name,
            register_components=register_components,
        )
        yield from dispatcher.walk(pages_path)


class FilesystemTreeDispatcher:
    """Run one depth-first walk: routes per node or skip component folders."""

    def __init__(
        self,
        skip_dir_names: Iterable[str],
        *,
        components_folder_name: str,
        register_components: bool,
    ) -> None:
        """Remember which dirs to skip and whether to register component roots."""
        self._skip_set = frozenset(skip_dir_names)
        self._components_folder_name = components_folder_name
        self._register_components = register_components

    def walk(self, pages_path: Path) -> Generator[tuple[str, Path], None, None]:
        """Yield ``(url_path, page_file)``. ``url_path`` is the route trail string."""
        yield from self._visit(pages_path, pages_path, "")

    def _visit(
        self,
        current_path: Path,
        tree_root: Path,
        url_path: str,
    ) -> Generator[tuple[str, Path], None, None]:
        try:
            items = list(current_path.iterdir())
        except OSError as e:
            logger.debug("Cannot list directory %s: %s", current_path, e)
            return
        for item in items:
            if item.is_dir():
                if item.name in self._skip_set:
                    if (
                        self._register_components
                        and item.name == self._components_folder_name
                    ):
                        _register_components_folder(
                            item,
                            tree_root,
                            url_path,
                        )
                    continue
                dir_name = item.name
                new_url_path = f"{url_path}/{dir_name}" if url_path else dir_name
                yield from self._visit(item, tree_root, new_url_path)
            elif item.name == "page.py":
                yield url_path, item

        if current_path.is_dir():
            page_file = current_path / "page.py"
            template_file = current_path / "template.djx"
            if not page_file.exists() and template_file.exists():
                yield url_path, current_path / "page.py"


def scan_pages_tree(
    pages_path: Path,
    skip_dir_names: Iterable[str] = (),
    *,
    components_folder_name: str = "_components",
    register_components: bool = False,
) -> Generator[tuple[str, Path], None, None]:
    """Walk a tree for ``page.py`` (and virtual pages) without a router instance."""
    dispatcher = FilesystemTreeDispatcher(
        skip_dir_names,
        components_folder_name=components_folder_name,
        register_components=register_components,
    )
    yield from dispatcher.walk(pages_path)


def _scan_pages_directory(
    pages_path: Path,
    skip_dir_names: Iterable[str] = (),
    *,
    components_folder_name: str = "_components",
    register_components: bool = False,
) -> Generator[tuple[str, Path], None, None]:
    """Yield the same pairs as ``scan_pages_tree``."""
    yield from scan_pages_tree(
        pages_path,
        skip_dir_names,
        components_folder_name=components_folder_name,
        register_components=register_components,
    )


def _register_components_folder(
    folder: Path,
    pages_root: Path,
    scope_relative: str,
) -> None:
    """Register one ``_components`` folder found during the file-router page walk."""
    import next.components as components_mod  # noqa: PLC0415

    components_mod.register_components_folder_from_router_walk(
        folder,
        pages_root,
        scope_relative,
    )


class RouterFactory:
    """Build ``RouterBackend`` instances from ``DEFAULT_PAGE_BACKENDS``-style dicts."""

    _backends: ClassVar[dict[str, type[RouterBackend]]] = {
        "next.urls.FileRouterBackend": FileRouterBackend,
    }

    @classmethod
    def register_backend(cls, name: str, backend_class: type[RouterBackend]) -> None:
        """Map a dotted backend path to a class for ``create_backend``."""
        cls._backends[name] = backend_class

    @classmethod
    def is_filesystem_discovery_router_class(cls, router_class: object) -> bool:
        """Return True if *router_class* implements the filesystem page-tree API."""
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
        """Whether *obj* is a router instance that walks pages trees (duck typing)."""
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
        # for other backend types, create with default initialization
        return backend_class()


class RouterManager:
    """Load ``RouterBackend`` instances from ``NEXT_FRAMEWORK`` and iterate them."""

    def __init__(self) -> None:
        """Empty backend list until first iteration."""
        self._backends: list[RouterBackend] = []
        self._config_cache: list[dict[str, Any]] | None = None

    def __repr__(self) -> str:
        """Debug representation with backend count."""
        return f"<{self.__class__.__name__} backends={len(self._backends)}>"

    def __len__(self) -> int:
        """Return the number of configured backends."""
        return len(self._backends)

    def __iter__(self) -> Generator[URLPattern | URLResolver, None, None]:
        """All patterns from each backend, loading config on first use."""
        if not self._backends:
            self._reload_config()

        for backend in self._backends:
            yield from backend.generate_urls()

    def __getitem__(self, index: int) -> RouterBackend:
        """Return the backend at the given index."""
        return self._backends[index]

    def _reload_config(self) -> None:
        """Reload backends from ``DEFAULT_PAGE_BACKENDS``."""
        self._config_cache = None
        self._backends.clear()

        configs = self._get_next_pages_config()
        for config in configs:
            try:
                if backend := RouterFactory.create_backend(config):
                    self._backends.append(backend)
            except Exception:
                logger.exception("error creating router from config %s", config)

    def _get_next_pages_config(self) -> list[dict[str, Any]]:
        """Router list from ``settings.NEXT_FRAMEWORK`` (merged defaults, cached)."""
        if self._config_cache is not None:
            return self._config_cache

        routers = next_framework_settings.DEFAULT_PAGE_BACKENDS
        if not isinstance(routers, list):
            self._config_cache = []
            return self._config_cache

        self._config_cache = routers
        return self._config_cache


# global router manager instance for application-wide URL pattern management
router_manager = RouterManager()


class _LazyUrlPatterns(list):
    """Defer expanding router and form patterns until first use.

    Avoids walking the tree at import time. Rebuilds from ``router_manager`` and
    ``form_action_manager`` on each access.
    Subclasses ``list`` so ``isinstance(urlpatterns, list)`` holds. Overrides
    ``__reversed__`` because the inherited empty internal buffer would break
    ``reversed(urlpatterns)`` in Django's URL resolver.
    """

    def _patterns(self) -> list[URLPattern | URLResolver]:
        return [*list(router_manager), *list(form_action_manager)]

    def __iter__(self) -> Iterator[URLPattern | URLResolver]:
        return iter(self._patterns())

    def __reversed__(self) -> Iterator[URLPattern | URLResolver]:
        return reversed(self._patterns())

    def __len__(self) -> int:
        return len(self._patterns())

    @overload
    def __getitem__(self, key: SupportsIndex, /) -> URLPattern | URLResolver:
        raise NotImplementedError

    @overload
    def __getitem__(self, key: slice, /) -> list[URLPattern | URLResolver]:
        raise NotImplementedError

    def __getitem__(
        self, key: SupportsIndex | slice, /
    ) -> URLPattern | URLResolver | list[URLPattern | URLResolver]:
        return self._patterns()[key]


# django URL configuration
app_name = "next"
urlpatterns = _LazyUrlPatterns()
