"""Build views and templates from page modules under each app pages tree.

Templates may come from a module template string or from template.djx files. Layout
chains use layout.djx. Context helpers merge into the render context. The page API
exposes URL wiring for the file router.
"""

import contextlib
import importlib.util
import inspect
import itertools
import logging
import types
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.template import Context as DjangoTemplateContext, Template
from django.urls import URLPattern, path
from django.utils.module_loading import import_string

from .conf import next_framework_settings
from .deps import DependencyResolver, RegisteredParameterProvider, resolver
from .utils import caller_source_path, classify_dirs_entries, resolve_base_dir


if TYPE_CHECKING:
    from .urls import URLPatternParser


logger = logging.getLogger(__name__)

_CONTEXT_DEFAULT_UNSET: object = object()


@dataclass(frozen=True, slots=True)
class Context:
    """Mark a page or layout parameter default so the value comes from context_data.

    Use Context as the default value of a parameter. A string source reads that key.
    An empty Context reads context_data using the parameter name. A callable source
    is called with dependency-injected arguments. Any other object is injected as a
    constant. The default= keyword supplies a fallback when the context key is
    missing.
    """

    source: object | None = None
    default: object = field(default=_CONTEXT_DEFAULT_UNSET, kw_only=True)


class ContextByDefaultProvider(RegisteredParameterProvider):
    """Resolve parameters that use a Context instance as their default."""

    def __init__(self, resolver: DependencyResolver) -> None:
        """Store the dependency resolver for callable Context sources."""
        self._resolver = resolver

    def can_handle(self, param: inspect.Parameter, _context: object) -> bool:
        """Return True when the parameter default is a Context instance."""
        return isinstance(param.default, Context)

    def resolve(self, param: inspect.Parameter, context: object) -> object:
        """Resolve the value from context_data, a callable, or a constant."""
        marker = param.default
        if not isinstance(marker, Context):
            return None

        source = marker.source
        context_data = getattr(context, "context_data", {}) or {}
        default_value: object = (
            None if marker.default is _CONTEXT_DEFAULT_UNSET else marker.default
        )

        if source is None:
            return context_data.get(param.name, default_value)

        if isinstance(source, str):
            return context_data.get(source, default_value)

        if callable(source):
            inner_ctx: dict[str, object] = {
                "request": getattr(context, "request", None),
                "form": getattr(context, "form", None),
                **(getattr(context, "url_kwargs", {}) or {}),
                "_cache": getattr(context, "cache", None),
                "_stack": getattr(context, "stack", None),
                "_context_data": context_data,
            }
            resolved = self._resolver.resolve_dependencies(source, **inner_ctx)
            return source(**resolved)

        # Constant value mode.
        return source


class ContextByNameProvider(RegisteredParameterProvider):
    """Inject context_data values when the parameter name is already a key."""

    def can_handle(self, param: inspect.Parameter, context: object) -> bool:
        """Return True when context_data already contains this parameter name."""
        context_data = getattr(context, "context_data", {}) or {}
        return param.name in context_data

    def resolve(self, param: inspect.Parameter, context: object) -> object:
        """Return the value stored under the parameter name in context_data."""
        context_data = getattr(context, "context_data", {}) or {}
        return context_data[param.name]


def _import_context_processor(
    processor_path: str,
) -> Callable[[Any], dict[str, Any]] | None:
    """Import a context processor callable or return None if import fails."""
    try:
        processor = import_string(processor_path)
        # type check to ensure it's a callable
        if callable(processor):
            return processor  # type: ignore[no-any-return]
    except (ImportError, AttributeError) as e:
        logger.warning("Could not import context processor %s: %s", processor_path, e)
    return None


def _get_context_processors() -> list[Callable[[Any], dict[str, Any]]]:
    """Load merged context processors from Next routers and from Django TEMPLATES."""
    configs = next_framework_settings.DEFAULT_PAGE_BACKENDS
    if not isinstance(configs, list):
        configs = []
    from_next = [
        path
        for c in configs
        if isinstance(c, dict)
        for path in (c.get("OPTIONS", {}).get("context_processors") or [])
        if isinstance(path, str)
    ]
    templates = getattr(settings, "TEMPLATES", [])
    opts = templates[0].get("OPTIONS", {}) if templates else {}
    from_templates = (
        list(opts.get("context_processors", []))
        if isinstance(opts.get("context_processors"), list)
        else []
    )
    # Merge both sources with Next routers first.
    # dict.fromkeys preserves order and dedupes.
    processor_paths = list(dict.fromkeys(from_next + from_templates))
    return [p for path in processor_paths if (p := _import_context_processor(path))]


def get_pages_directories_for_watch() -> list[Path]:
    """Return absolute page tree roots that the autoreloader should consider."""
    # Import RouterFactory inside the function to avoid a circular import chain
    # between forms, pages, and urls.
    from next.urls import RouterFactory  # noqa: PLC0415

    configs = next_framework_settings.DEFAULT_PAGE_BACKENDS
    if not isinstance(configs, list):
        return []
    seen: set[Path] = set()
    result: list[Path] = []
    for config in configs:
        if not isinstance(config, dict):
            continue
        try:
            backend = RouterFactory.create_backend(config)
        except Exception:
            logger.exception(
                "error creating backend for watch dirs from config %s", config
            )
            continue
        if not RouterFactory.is_filesystem_discovery_router(backend):
            continue
        fs_backend: Any = backend
        for p in itertools.chain(
            (p.resolve() for p in fs_backend._get_root_pages_paths()),
            (
                a.resolve()
                for app_name in fs_backend._get_installed_apps()
                if (a := fs_backend._get_app_pages_path(app_name))
            ),
        ):
            if p not in seen:
                seen.add(p.resolve())
                result.append(p.resolve())
    return result


def iter_pages_roots_with_components_folder_names() -> list[tuple[Path, str]]:
    """List distinct page root and components folder name pairs for autoreload globs."""
    from next.urls import RouterFactory  # noqa: PLC0415

    configs = next_framework_settings.DEFAULT_PAGE_BACKENDS
    if not isinstance(configs, list):
        return []
    seen: set[tuple[Path, str]] = set()
    result: list[tuple[Path, str]] = []
    for config in configs:
        if not isinstance(config, dict):
            continue
        try:
            backend = RouterFactory.create_backend(config)
        except Exception:
            logger.exception(
                "error creating backend for components watch globs from config %s",
                config,
            )
            continue
        if not RouterFactory.is_filesystem_discovery_router(backend):
            continue
        fs_backend: Any = backend
        comp_name = str(fs_backend._components_folder_name)
        for p in itertools.chain(
            (p.resolve() for p in fs_backend._get_root_pages_paths()),
            (
                a.resolve()
                for app_name in fs_backend._get_installed_apps()
                if (a := fs_backend._get_app_pages_path(app_name))
            ),
        ):
            key = (p, comp_name)
            if key not in seen:
                seen.add(key)
                result.append((p, comp_name))
    return result


def get_layout_djx_paths_for_watch() -> set[Path]:
    """Return every ``layout.djx`` path under page trees (autoreload checks)."""
    result: set[Path] = set()
    for pages_path in get_pages_directories_for_watch():
        try:
            for path in pages_path.rglob("layout.djx"):
                result.add(path.resolve())
        except OSError as e:
            logger.debug("Cannot rglob layout.djx under %s: %s", pages_path, e)
    return result


def get_template_djx_paths_for_watch() -> set[Path]:
    """Return every ``template.djx`` path under page trees (autoreload checks)."""
    result: set[Path] = set()
    for pages_path in get_pages_directories_for_watch():
        try:
            for path in pages_path.rglob("template.djx"):
                result.add(path.resolve())
        except OSError as e:
            logger.debug("Cannot rglob template.djx under %s: %s", pages_path, e)
    return result


def _load_python_module(file_path: Path) -> types.ModuleType | None:
    """Load ``file_path`` as a module or return ``None``."""
    try:
        spec = importlib.util.spec_from_file_location("page_module", file_path)
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (ImportError, AttributeError, OSError, SyntaxError) as e:
        logger.debug("Could not load module %s: %s", file_path, e)
        return None
    else:
        return module


def _read_string_list(module: types.ModuleType, attr: str) -> list[str]:
    """Return a module-level string sequence attribute or an empty list."""
    value = getattr(module, attr, None)
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


class TemplateLoader(ABC):
    """Pluggable source of template text for a ``page.py`` path."""

    @abstractmethod
    def can_load(self, file_path: Path) -> bool:
        """Whether this loader applies without heavy work."""

    @abstractmethod
    def load_template(self, file_path: Path) -> str | None:
        """Template source string, or ``None`` if unavailable."""


class PythonTemplateLoader(TemplateLoader):
    """Template from ``page.py`` when the module defines ``template``."""

    def can_load(self, file_path: Path) -> bool:
        """Return whether the module loads and defines ``template``."""
        module = _load_python_module(file_path)
        return module is not None and hasattr(module, "template")

    def load_template(self, file_path: Path) -> str | None:
        """``module.template`` if present."""
        module = _load_python_module(file_path)
        return getattr(module, "template", None) if module else None


class DjxTemplateLoader(TemplateLoader):
    """Template from ``template.djx`` beside ``page.py``."""

    def can_load(self, file_path: Path) -> bool:
        """Sibling ``template.djx`` exists."""
        return (file_path.parent / "template.djx").exists()

    def load_template(self, file_path: Path) -> str | None:
        """File contents of ``template.djx``."""
        djx_file = file_path.parent / "template.djx"
        try:
            return djx_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None


class LayoutTemplateLoader(TemplateLoader):
    """Wraps page content in outer ``layout.djx`` files up the directory chain."""

    def can_load(self, file_path: Path) -> bool:
        """At least one ``layout.djx`` exists on the path to the template."""
        return self._find_layout_files(file_path) is not None

    def load_template(self, file_path: Path) -> str | None:
        """Nested layout files with the page block inserted into the innermost slot."""
        layout_files = self._find_layout_files(file_path)
        if not layout_files:
            return None

        # wrap template content in template block
        template_content = self._wrap_in_template_block(file_path)

        # compose layout hierarchy
        return self._compose_layout_hierarchy(template_content, layout_files)

    def _find_layout_files(self, file_path: Path) -> list[Path] | None:
        """``layout.djx`` paths from near to far, plus optional global layouts."""
        layout_files = []
        current_dir = file_path.parent

        # check current directory first, then walk up the directory tree
        while current_dir != current_dir.parent:  # not at root
            layout_file = current_dir / "layout.djx"
            if layout_file.exists():
                layout_files.append(layout_file)
            current_dir = current_dir.parent

        # also add additional layouts from other configured pages router dirs
        # but only if they're not already in the local hierarchy
        if additional_layouts := self._get_additional_layout_files():
            for additional_layout in additional_layouts:
                if additional_layout not in layout_files:
                    layout_files.append(additional_layout)

        return layout_files or None

    def _get_additional_layout_files(self) -> list[Path]:
        """Root-level ``layout.djx`` from each page backend ``DIRS`` path root."""
        configs = next_framework_settings.DEFAULT_PAGE_BACKENDS or []
        if not isinstance(configs, list):
            configs = []
        candidates = (
            layout
            for c in configs
            if isinstance(c, dict)
            for d in self._get_pages_dirs_for_config(c)
            if d.exists() and (layout := d / "layout.djx").exists()
        )
        return list(dict.fromkeys(candidates))

    def _get_pages_dirs_for_config(self, config: dict) -> list[Path]:
        """Candidate roots from one router ``DIRS`` entry (path-like only)."""
        path_roots, _ = classify_dirs_entries(
            list(config.get("DIRS") or []),
            resolve_base_dir(),
        )
        return list(path_roots)

    def _wrap_in_template_block(self, file_path: Path) -> str:
        """Page body wrapped in ``{% block template %}`` when needed."""
        template_file = file_path.parent / "template.djx"
        if template_file.exists():
            with contextlib.suppress(OSError, UnicodeDecodeError):
                content = template_file.read_text(encoding="utf-8")
                # check if there's a layout file in the same directory
                layout_file = file_path.parent / "layout.djx"
                if layout_file.exists():
                    # template is already wrapped in layout, return as-is
                    return content
                return f"{{% block template %}}{content}{{% endblock template %}}"
        return "{% block template %}{% endblock template %}"

    def _compose_layout_hierarchy(
        self,
        template_content: str,
        layout_files: list[Path],
    ) -> str:
        """Outermost layout last. Page replaces the first matching template block."""
        result = template_content

        # process all layout files in order (local layouts come first due to
        # how _find_layout_files builds the list)
        for layout_file in layout_files:
            with contextlib.suppress(OSError, UnicodeDecodeError):
                layout_content = layout_file.read_text(encoding="utf-8")
                for placeholder in (
                    "{% block template %}{% endblock template %}",
                    "{% block template %}{% endblock %}",
                ):
                    if placeholder in layout_content:
                        result = layout_content.replace(placeholder, result, 1)
                        break
        return result


class LayoutManager:
    """Caches composed layout strings per page path."""

    def __init__(self) -> None:
        """Empty layout cache."""
        self._layout_registry: dict[Path, str] = {}
        self._layout_loader = LayoutTemplateLoader()

    def discover_layouts_for_template(self, template_path: Path) -> str | None:
        """Compose and store layout text when ``LayoutTemplateLoader`` applies."""
        if not self._layout_loader.can_load(template_path):
            return None

        composed_template = self._layout_loader.load_template(template_path)
        if composed_template:
            self._layout_registry[template_path] = composed_template

        return composed_template

    def get_layout_template(self, template_path: Path) -> str | None:
        """Return the cached composed template for ``template_path``."""
        return self._layout_registry.get(template_path)

    def clear_registry(self) -> None:
        """Drop all cached layout strings."""
        self._layout_registry.clear()


@dataclass(frozen=True, slots=True)
class ContextResult:
    """Holds the template context dict and the JavaScript-serializable subset.

    ``context_data`` contains all values to be merged into the Django template
    context. ``js_context`` contains only the subset marked ``serialize=True``
    and is later passed to ``Next._init`` via ``StaticCollector.add_js_context``.
    """

    context_data: dict[str, Any]
    js_context: dict[str, Any]


class PageContextRegistry:
    """Registers per-``page.py`` context callables and merges their output."""

    def __init__(
        self,
        resolver: DependencyResolver | None = None,
    ) -> None:
        """Initialize with an optional resolver and an empty registry."""
        self._context_registry: dict[
            Path,
            dict[str | None, tuple[Callable[..., Any], bool, bool]],
        ] = {}
        self._resolver = resolver

    def _get_resolver(self) -> DependencyResolver:
        """Injected resolver or the shared ``resolver``."""
        if self._resolver is not None:
            return self._resolver
        return resolver

    def register_context(
        self,
        file_path: Path,
        key: str | None,
        func: Callable[..., Any],
        *,
        inherit_context: bool = False,
        serialize: bool = False,
    ) -> None:
        """Bind ``func`` to ``file_path``. Keyed vs dict-merge semantics by ``key``."""
        self._context_registry.setdefault(file_path, {})[key] = (
            func,
            inherit_context,
            serialize,
        )

    def collect_context(
        self,
        file_path: Path,
        *args: object,
        **kwargs: object,
    ) -> ContextResult:
        """Merge inherited layout context with this file's context callables.

        Returns a ``ContextResult`` that separates the full template context from
        the JavaScript-serializable subset. The js_context uses first-registration
        semantics so that page-level values always take priority over inherited ones.
        """
        request = args[0] if args and isinstance(args[0], HttpRequest) else None
        context_data: dict[str, Any] = {}
        js_context: dict[str, Any] = {}
        dep_cache: dict[str, Any] = {}
        dep_stack: list[str] = []

        # collect inherited context from layout directories first (lower priority)
        inherited_context = self._collect_inherited_context(
            file_path, request, kwargs, dep_cache, dep_stack
        )
        context_data.update(inherited_context)

        # current file: None first, then by key
        registry = self._context_registry.get(file_path, {})
        ordered = sorted(
            registry.items(),
            key=lambda item: (item[0] is not None, str(item[0] or "")),
        )
        for key, (func, _, serialize) in ordered:
            resolved = self._get_resolver().resolve_dependencies(
                func,
                request=request,
                _cache=dep_cache,
                _stack=dep_stack,
                _context_data=context_data,
                **kwargs,
            )
            result = func(**resolved)
            if key is None:
                context_data.update(result)
                if serialize:
                    for k, v in result.items():
                        if k not in js_context:
                            js_context[k] = v
            else:
                context_data[key] = result
                if serialize and key not in js_context:
                    js_context[key] = result

        return ContextResult(context_data=context_data, js_context=js_context)

    def _collect_inherited_context(
        self,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: dict[str, object],
        dep_cache: dict[str, Any],
        dep_stack: list[str],
    ) -> dict[str, Any]:
        """Values from parent ``page.py`` callables marked ``inherit_context``."""
        inherited_context = {}
        current_dir = file_path.parent

        # walk up the directory tree to find layout directories
        while current_dir != current_dir.parent:  # not at root
            layout_file = current_dir / "layout.djx"
            page_file = current_dir / "page.py"

            # if layout.djx exists, check for page.py with inheritable context
            if layout_file.exists() and page_file.exists():
                for key, (func, inherit_context, _) in self._context_registry.get(
                    page_file,
                    {},
                ).items():
                    if inherit_context:
                        resolved = self._get_resolver().resolve_dependencies(
                            func,
                            request=request,
                            _cache=dep_cache,
                            _stack=dep_stack,
                            **url_kwargs,
                        )
                        if key is None:
                            inherited_context.update(func(**resolved))
                        else:
                            inherited_context[key] = func(**resolved)

            current_dir = current_dir.parent

        return inherited_context


class Page:
    """Template loading, context, layouts, rendering, and URL pattern wiring."""

    def __init__(self) -> None:
        """Fresh registries and the default loader chain."""
        self._template_registry: dict[Path, str] = {}
        self._template_source_mtimes: dict[Path, dict[Path, float]] = {}
        self._resolver: DependencyResolver | None = None
        self._context_manager = PageContextRegistry(None)
        self._layout_manager = LayoutManager()
        self._template_loaders = [
            PythonTemplateLoader(),
            DjxTemplateLoader(),
            LayoutTemplateLoader(),
        ]

    def _get_resolver(self) -> DependencyResolver:
        """Shared ``resolver`` instance."""
        return resolver

    def register_template(self, file_path: Path, template_str: str) -> None:
        """Store rendered template source for ``file_path``."""
        self._template_registry[file_path] = template_str

    def _get_caller_path(self, back_count: int = 1) -> Path:
        """Filesystem path of the caller outside ``pages.py`` (for ``@context``)."""
        return caller_source_path(
            back_count=back_count,
            max_walk=10,
            skip_while_filename_endswith=("pages.py",),
        )

    def context(
        self,
        func_or_key: Callable[..., Any] | str | None = None,
        *,
        inherit_context: bool = False,
        serialize: bool = False,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register keyed or dict-merge ``@context`` for the caller file.

        Pass ``serialize=True`` to include the return value in ``Next.context``
        so JavaScript code on the page can read it via ``window.Next.context``.
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if callable(func_or_key):
                # @context usage - function returns dict
                caller_path = self._get_caller_path(2)
                self._context_manager.register_context(
                    caller_path,
                    None,
                    func_or_key,
                    inherit_context=inherit_context,
                    serialize=serialize,
                )
            else:
                # @context("key") usage - function result stored under key
                caller_path = self._get_caller_path(1)
                self._context_manager.register_context(
                    caller_path,
                    func_or_key,
                    func,
                    inherit_context=inherit_context,
                    serialize=serialize,
                )
            return func

        return decorator(func_or_key) if callable(func_or_key) else decorator

    def build_render_context(
        self,
        file_path: Path,
        *args: object,
        **kwargs: object,
    ) -> dict[str, object]:
        """Build render context: path, kwargs, ``@context``, request, processors.

        The returned dict includes ``_next_js_context`` holding the subset of
        values marked ``serialize=True``. The caller (``render``) pops that key
        and seeds the ``StaticCollector`` with it before creating the template context.
        """
        context_data: dict[str, object] = {}
        template_djx = file_path.parent / "template.djx"
        context_data["current_template_path"] = (
            str(template_djx) if template_djx.exists() else str(file_path)
        )
        context_data["current_page_module_path"] = str(file_path.resolve())
        context_data.update(kwargs)

        context_result = self._context_manager.collect_context(
            file_path, *args, **kwargs
        )
        context_data.update(context_result.context_data)
        context_data["_next_js_context"] = context_result.js_context

        request: HttpRequest | None = None
        if args and isinstance(args[0], HttpRequest):
            request = args[0]

        if request is not None:
            context_data["request"] = request

        context_processors = _get_context_processors()
        if request and context_processors:
            for processor in context_processors:
                try:
                    processor_data = processor(request)
                    if isinstance(processor_data, dict):
                        context_data.update(processor_data)
                except (TypeError, ValueError, AttributeError, KeyError) as e:
                    logger.warning(
                        "Error in context processor %s: %s",
                        processor.__name__,
                        e,
                    )

        return context_data

    def render(self, file_path: Path, *args: object, **kwargs: object) -> str:
        """Render with Django ``Template`` after reloading stale sources and context.

        A ``StaticCollector`` is placed in the template context before rendering
        so that nested components can register their CSS and JS references. Once
        the template has rendered, placeholder markers left by
        ``{% collect_styles %}`` and ``{% collect_scripts %}`` are replaced with
        actual ``<link>`` and ``<script>`` tags.
        """
        if file_path not in self._template_registry or self._is_template_stale(
            file_path
        ):
            self._template_registry.pop(file_path, None)
            self._template_source_mtimes.pop(file_path, None)
            self._load_template_for_file(file_path)
            self._record_template_source_mtimes(file_path)
        template_str = self._template_registry[file_path]
        context_data = self.build_render_context(file_path, *args, **kwargs)

        from .static import StaticCollector, default_manager  # noqa: PLC0415

        collector = StaticCollector()
        js_context: dict[str, object] = context_data.pop("_next_js_context", {})  # type: ignore[assignment]
        for js_key, js_value in js_context.items():
            collector.add_js_context(js_key, js_value)
        default_manager.discover_page_assets(file_path, collector)
        context_data["_static_collector"] = collector

        html = Template(template_str).render(DjangoTemplateContext(context_data))
        return cast("str", default_manager.inject(html, collector, page_path=file_path))

    def _create_view_function(
        self,
        file_path: Path,
        _parameters: dict[str, str],
    ) -> Callable[..., HttpResponse]:
        """Callable view: ``HttpResponse`` with ``render`` output for this page."""

        def view(request: HttpRequest, **kwargs: object) -> HttpResponse:
            # kwargs already contains real parameter values from URL (e.g., id=999)
            # parameters dict is just a mapping and shouldn't overwrite real values
            content = self.render(file_path, request, **kwargs)
            return HttpResponse(content)

        return view

    def has_template(
        self, file_path: Path, module: types.ModuleType | None = None
    ) -> bool:
        """Return whether loaders can supply a template (reuse ``module`` if passed)."""
        if self._layout_manager._layout_loader.can_load(file_path):
            return True
        for loader in self._template_loaders:
            if isinstance(loader, LayoutTemplateLoader):
                continue
            if isinstance(loader, PythonTemplateLoader):
                if module is not None and hasattr(module, "template"):
                    return True
                continue
            if loader.can_load(file_path):
                return True
        return False

    def _load_template_for_file(self, file_path: Path) -> bool:
        """Populate ``_template_registry`` from layout or inline loaders."""
        # try layout template loader first (priority for layout inheritance)
        if self._layout_manager.discover_layouts_for_template(file_path):
            layout_template = self._layout_manager.get_layout_template(file_path)
            if layout_template:
                self.register_template(file_path, layout_template)
                return True

        # try regular template loaders
        for loader in self._template_loaders:
            if isinstance(loader, LayoutTemplateLoader):
                continue  # already handled above
            if loader.can_load(file_path):
                template_content = loader.load_template(file_path)
                if template_content:
                    self.register_template(file_path, template_content)
                    return True
        return False

    def _get_template_source_paths(self, file_path: Path) -> list[Path]:
        """``template.djx`` and layout files that define this page's source."""
        template_djx = file_path.parent / "template.djx"
        layout_files = self._layout_manager._layout_loader._find_layout_files(file_path)
        return ([template_djx] if template_djx.exists() else []) + (layout_files or [])

    def _record_template_source_mtimes(self, file_path: Path) -> None:
        """Snapshot mtimes for stale detection."""
        paths = self._get_template_source_paths(file_path)
        if not paths:
            return
        mtimes: dict[Path, float] = {}
        for p in paths:
            with contextlib.suppress(OSError):
                mtimes[p] = p.stat().st_mtime
        if mtimes:
            self._template_source_mtimes[file_path] = mtimes

    def _is_template_stale(self, file_path: Path) -> bool:
        """Whether any tracked source file changed on disk."""
        stored = self._template_source_mtimes.get(file_path)
        if not stored:
            return False
        for p, old_mtime in stored.items():
            try:
                if p.stat().st_mtime > old_mtime:
                    return True
            except OSError as e:
                logger.debug("Cannot stat %s in stale check: %s", p, e)
        return False

    def _create_regular_page_pattern(
        self,
        file_path: Path,
        django_pattern: str,
        parameters: dict[str, str],
        clean_name: str,
    ) -> URLPattern | None:
        """URL for a real ``page.py``: template view or ``render`` with DI."""
        module = _load_python_module(file_path)
        if not module:
            return None

        # Try template-based rendering first as a check only.
        # Do not load .djx content here.
        if self.has_template(file_path, module):
            view = self._create_view_function(file_path, parameters)
            return path(
                django_pattern,
                view,
                name=next_framework_settings.URL_NAME_TEMPLATE.format(name=clean_name),
            )

        # fall back to custom render function with DI wrapper
        if (render_func := getattr(module, "render", None)) and callable(render_func):
            view = self._create_render_wrapper(render_func)
            return path(
                django_pattern,
                view,
                name=next_framework_settings.URL_NAME_TEMPLATE.format(name=clean_name),
            )

        return None

    def _create_render_wrapper(
        self, render_func: Callable[..., HttpResponse | str]
    ) -> Callable[..., HttpResponse]:
        """Wrap custom render so it is called with resolved dependencies only."""

        def view(request: HttpRequest, **kwargs: object) -> HttpResponse:
            dep_cache: dict[str, Any] = {}
            dep_stack: list[str] = []
            resolved = self._get_resolver().resolve_dependencies(
                render_func,
                request=request,
                _cache=dep_cache,
                _stack=dep_stack,
                **kwargs,
            )
            result = render_func(**resolved)
            if isinstance(result, str):
                return HttpResponse(result)
            return result

        return view

    def _create_virtual_page_pattern(
        self,
        file_path: Path,
        django_pattern: str,
        parameters: dict[str, str],
        clean_name: str,
    ) -> URLPattern | None:
        """URL when only ``template.djx`` exists (no ``page.py``)."""
        if self.has_template(file_path, module=None):
            view = self._create_view_function(file_path, parameters)
            return path(
                django_pattern,
                view,
                name=next_framework_settings.URL_NAME_TEMPLATE.format(name=clean_name),
            )
        return None

    def create_url_pattern(
        self,
        url_path: str,
        file_path: Path,
        url_parser: "URLPatternParser",
    ) -> URLPattern | None:
        """``path()`` for a page: template view, custom ``render``, or virtual."""
        django_pattern, parameters = url_parser.parse_url_pattern(url_path)
        clean_name = url_parser.prepare_url_name(url_path)

        if file_path.exists():
            return self._create_regular_page_pattern(
                file_path,
                django_pattern,
                parameters,
                clean_name,
            )
        return self._create_virtual_page_pattern(
            file_path,
            django_pattern,
            parameters,
            clean_name,
        )


# global singleton instance for application-wide page management
page = Page()

# convenience alias for the context decorator
context = page.context
