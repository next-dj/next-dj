"""`Page` manager and its process-wide singleton.

`Page` orchestrates template loading, context collection, layout
composition, rendering, and URL-pattern wiring. `page` is the
application-wide singleton. `context` is a convenience alias for
`page.context` used by the `@context` decorator in user code.
"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, Any, cast

from django.http import HttpRequest, HttpResponse
from django.template import Context as DjangoTemplateContext, Template
from django.urls import URLPattern, path

from next.conf import next_framework_settings
from next.deps import DependencyResolver, resolver
from next.utils import caller_source_path

from .loaders import (
    DjxTemplateLoader,
    LayoutManager,
    LayoutTemplateLoader,
    PythonTemplateLoader,
    _load_python_module,
)
from .processors import _get_context_processors
from .registry import PageContextRegistry


if TYPE_CHECKING:
    import types
    from collections.abc import Callable
    from pathlib import Path

    from next.urls import URLPatternParser


logger = logging.getLogger(__name__)


class Page:
    """Coordinate template loading, context, layouts, rendering, and URL wiring."""

    def __init__(self) -> None:
        """Initialise fresh registries and the default loader chain."""
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
        """Return the shared `resolver` singleton."""
        return resolver

    def register_template(self, file_path: Path, template_str: str) -> None:
        """Store rendered template source for `file_path`."""
        self._template_registry[file_path] = template_str

    def _get_caller_path(self, back_count: int = 1) -> Path:
        """Return the filesystem path of the user caller outside this module."""
        return caller_source_path(
            back_count=back_count,
            max_walk=10,
            skip_while_filename_endswith=("manager.py",),
        )

    def context(
        self,
        func_or_key: Callable[..., Any] | str | None = None,
        *,
        inherit_context: bool = False,
        serialize: bool = False,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a keyed or dict-merge `@context` for the caller file.

        Pass `serialize=True` to include the return value in
        `Next.context` so JavaScript code on the page can read it via
        `window.Next.context`.
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if callable(func_or_key):
                caller_path = self._get_caller_path(2)
                self._context_manager.register_context(
                    caller_path,
                    None,
                    func_or_key,
                    inherit_context=inherit_context,
                    serialize=serialize,
                )
            else:
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
        """Build the full render context dict used by `render`.

        The returned dict includes `_next_js_context` holding the subset
        of values marked `serialize=True`. `render` pops that key and
        seeds the `StaticCollector` with it before creating the Django
        template context.
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
        """Render the page with Django `Template`, static collector included.

        A `StaticCollector` is placed in the template context before
        rendering so that nested components can register CSS and JS
        references. Once the template has rendered, placeholder markers
        left by `{% collect_styles %}` and `{% collect_scripts %}` are
        replaced with actual `<link>` and `<script>` tags.
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

        from next.static import StaticCollector, default_manager  # noqa: PLC0415

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
        """Return a view callable that renders the page for this `file_path`."""

        def view(request: HttpRequest, **kwargs: object) -> HttpResponse:
            content = self.render(file_path, request, **kwargs)
            return HttpResponse(content)

        return view

    def has_template(
        self, file_path: Path, module: types.ModuleType | None = None
    ) -> bool:
        """Return whether any loader can supply a template for this path."""
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
        """Populate `_template_registry` from layout or inline loaders."""
        if self._layout_manager.discover_layouts_for_template(file_path):
            layout_template = self._layout_manager.get_layout_template(file_path)
            if layout_template:
                self.register_template(file_path, layout_template)
                return True

        for loader in self._template_loaders:
            if isinstance(loader, LayoutTemplateLoader):
                continue
            if loader.can_load(file_path):
                template_content = loader.load_template(file_path)
                if template_content:
                    self.register_template(file_path, template_content)
                    return True
        return False

    def _get_template_source_paths(self, file_path: Path) -> list[Path]:
        """Return `template.djx` and layout files that back this page."""
        template_djx = file_path.parent / "template.djx"
        layout_files = self._layout_manager._layout_loader._find_layout_files(file_path)
        return ([template_djx] if template_djx.exists() else []) + (layout_files or [])

    def _record_template_source_mtimes(self, file_path: Path) -> None:
        """Snapshot mtimes of template source files for stale detection."""
        import contextlib  # noqa: PLC0415

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
        """Return whether any tracked source file changed on disk."""
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
        """Return the URL pattern for a real `page.py` with a template or `render`."""
        module = _load_python_module(file_path)
        if not module:
            return None

        if self.has_template(file_path, module):
            view = self._create_view_function(file_path, parameters)
            return path(
                django_pattern,
                view,
                name=next_framework_settings.URL_NAME_TEMPLATE.format(name=clean_name),
            )

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
        """Return a view that calls a user `render` with DI-resolved arguments."""

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
        """Return the URL pattern for a template-only page without `page.py`."""
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
        url_parser: URLPatternParser,
    ) -> URLPattern | None:
        """Return a `path()` pattern for a page, template, or virtual entry."""
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


_ = inspect  # keep `inspect` import reachable for test-time patching


page: Page = Page()
context = page.context
