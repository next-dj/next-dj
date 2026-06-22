"""`Page` manager and its process-wide singleton.

`Page` orchestrates template loading, context collection, layout
composition, rendering, and URL-pattern wiring. `page` is the
application-wide singleton. `context` is a convenience alias for
`page.context` used by the `@context` decorator in user code.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.template import Context as DjangoTemplateContext, Template
from django.urls import URLPattern, path

from next.conf import next_framework_settings
from next.deps import DependencyResolver, resolver
from next.utils import caller_source_path

from .loaders import (
    LayoutManager,
    _load_python_module_memo,
    build_registered_loaders,
)
from .processors import _get_context_processors
from .registry import PageContextRegistry
from .signals import page_rendered, template_loaded


if TYPE_CHECKING:
    import types
    from collections.abc import Callable
    from pathlib import Path

    from next.static import StaticCollector
    from next.static.serializers import JsContextSerializer
    from next.urls import URLPatternParser


logger = logging.getLogger(__name__)


class _RoutedPageView(Protocol):
    """A page view carrying the source path form dispatch resolves back to."""

    next_page_path: Path

    def __call__(self, request: HttpRequest, **kwargs: object) -> HttpResponseBase: ...


@dataclass(frozen=True, slots=True)
class _BodyResolution:
    """Per-request outcome of `Page._resolve_page_body`.

    `body` is a string that will be composed through the layout chain
    and rendered. `http_response` is a Django response that is returned
    verbatim. The framework uses the verbatim path as the `render()`
    escape hatch for redirects, streaming responses, JSON, and anything
    else. The type is `HttpResponseBase` so `StreamingHttpResponse` and
    `FileResponse` flow through unchanged alongside `HttpResponse`.

    `dynamic` marks a body produced by a `render()` function returning a
    string. Such a body never reaches the composed-template cache, so a
    zone in it has no compiled source to render standalone.
    """

    body: str | None = None
    http_response: HttpResponseBase | None = None
    dynamic: bool = False


class Page:
    """Coordinate template loading, context, layouts, rendering, and URL wiring."""

    def __init__(self) -> None:
        """Initialise fresh registries and layout manager.

        File-based template loaders are not held as an instance
        attribute. The module-level `build_registered_loaders()` helper
        caches them and invalidates on `settings_reloaded`.
        """
        self._template_registry: dict[Path, str] = {}
        self._compiled_registry: dict[Path, Template] = {}
        self._template_source_mtimes: dict[Path, dict[Path, float]] = {}
        self._resolver: DependencyResolver | None = None
        self._context_manager = PageContextRegistry(None)
        self._layout_manager = LayoutManager()

    def _get_resolver(self) -> DependencyResolver:
        """Return the shared `resolver` singleton."""
        return resolver

    def register_template(self, file_path: Path, template_str: str) -> None:
        """Store rendered template source for `file_path`.

        The compiled-template entry is dropped alongside so every write
        to the source registry invalidates the compiled layer with it.
        """
        self._template_registry[file_path] = template_str
        self._compiled_registry.pop(file_path, None)
        template_loaded.send(sender=Page, file_path=file_path)

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
        serializer: JsContextSerializer | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register a keyed or dict-merge `@context` for the caller file.

        Pass `serialize=True` to include the return value in
        `Next.context` so JavaScript code on the page can read it via
        `window.Next.context`. Pass `serializer=` to route this key
        through a custom `JsContextSerializer` instead of the global
        `JS_CONTEXT_SERIALIZER` setting.
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
                    serializer=serializer,
                )
            else:
                caller_path = self._get_caller_path(1)
                self._context_manager.register_context(
                    caller_path,
                    func_or_key,
                    func,
                    inherit_context=inherit_context,
                    serialize=serialize,
                    serializer=serializer,
                )
            return func

        return decorator(func_or_key) if callable(func_or_key) else decorator

    def build_render_context(
        self,
        file_path: Path,
        request: HttpRequest | None = None,
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
            file_path, request, **kwargs
        )
        context_data.update(context_result.context_data)
        context_data["_next_js_context"] = context_result.js_context
        context_data["_next_js_context_serializers"] = (
            context_result.js_context_serializers
        )

        if request is not None:
            context_data["request"] = request

        context_processors = _get_context_processors()
        if request and context_processors:
            strict = bool(getattr(next_framework_settings, "STRICT_CONTEXT", False))
            for processor in context_processors:
                try:
                    processor_data = processor(request)
                    if isinstance(processor_data, dict):
                        context_data.update(processor_data)
                except (TypeError, ValueError, AttributeError, KeyError) as e:
                    if strict:
                        raise
                    logger.warning(
                        "Error in context processor %s: %s",
                        processor.__name__,
                        e,
                    )

        return context_data

    def _load_static_body(
        self,
        file_path: Path,
        module: types.ModuleType | None,
    ) -> str:
        """Return the static body for `file_path` without invoking `render()`.

        The `module.template` attribute wins when set to a non-`None`
        string. Otherwise the framework consults registered
        `TemplateLoader` instances in the order declared under
        `NEXT_FRAMEWORK["TEMPLATE_LOADERS"]`. The first loader that can
        load the path returns the body. An empty string is returned
        when no source is present so an ancestor layout can still
        render with an empty slot.
        """
        if module is not None:
            template_attr = getattr(module, "template", None)
            if isinstance(template_attr, str):
                return template_attr
        for loader in build_registered_loaders():
            if loader.can_load(file_path):
                return loader.load_template(file_path) or ""
        return ""

    def _resolve_page_body(
        self,
        file_path: Path,
        module: types.ModuleType | None,
        request: HttpRequest | None = None,
        **kwargs: object,
    ) -> _BodyResolution:
        """Resolve the page body per-request.

        The resolution order is `render()`, then the `template` module
        attribute, then the registered `TemplateLoader` chain, then an
        empty body. `render()` may short-circuit by returning any
        `HttpResponseBase` subclass such as a redirect, a streaming
        response, a file response, or a JSON response. In that case
        the layout and static pipelines are bypassed entirely.
        """
        if module is not None:
            render_func = getattr(module, "render", None)
            if callable(render_func):
                return self._call_render_function(
                    render_func, file_path, request, **kwargs
                )
        return _BodyResolution(body=self._load_static_body(file_path, module))

    def _call_render_function(
        self,
        render_func: Callable[..., object],
        file_path: Path,
        request: HttpRequest | None = None,
        **kwargs: object,
    ) -> _BodyResolution:
        """Invoke `render_func` with DI-resolved arguments and classify the result."""
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
        if isinstance(result, HttpResponseBase):
            return _BodyResolution(http_response=result)
        if isinstance(result, str):
            return _BodyResolution(body=result, dynamic=True)
        msg = (
            f"page.py render() at {file_path} must return str or "
            f"HttpResponseBase, got {type(result).__name__}."
        )
        raise TypeError(msg)

    def render_with_static_assets(
        self,
        file_path: Path,
        template: Template | str,
        context_data: dict[str, object],
        *,
        request: HttpRequest | None = None,
    ) -> tuple[str, StaticCollector]:
        """Render `template` and inject collected static assets.

        `template` is either a precompiled `Template` (reused as-is) or
        raw template source, which is parsed before rendering.

        The method seeds a fresh `StaticCollector`, hydrates it with
        the JS context that `build_render_context` left under the
        `_next_js_context` key, discovers co-located assets for the
        page, renders the Django template, and replaces placeholders
        through `default_manager.inject`. The active `request` reaches
        the static backend so request-aware subclasses can rewrite
        URLs. Both the rendered HTML and the collector are returned so
        callers can reuse the collector for telemetry without a second
        rendering pass. Suitable for the canonical page render path
        and for partial paths such as form-error rerenders.
        """
        # next.static imports next.pages.manager, so the static manager import
        # is deferred here to break the next.pages <-> next.static cycle.
        from next.static import default_manager  # noqa: PLC0415

        collector = default_manager.create_collector()
        js_context: dict[str, object] = context_data.pop("_next_js_context", {})  # type: ignore[assignment]
        js_serializers: dict[str, JsContextSerializer] = context_data.pop(
            "_next_js_context_serializers", {}
        )  # type: ignore[assignment]
        for js_key, js_value in js_context.items():
            collector.add_js_context(
                js_key, js_value, serializer=js_serializers.get(js_key)
            )
        default_manager.discover_page_assets(file_path, collector)
        context_data["_static_collector"] = collector

        compiled = template if isinstance(template, Template) else Template(template)
        html = compiled.render(DjangoTemplateContext(context_data))
        result = cast(
            "str",
            default_manager.inject(
                html, collector, page_path=file_path, request=request
            ),
        )
        return result, collector

    def _render_template_str(
        self,
        file_path: Path,
        template: Template | str,
        start: float,
        request: HttpRequest | None = None,
        **kwargs: object,
    ) -> str:
        """Build context, render `template`, inject static assets, emit signal."""
        context_data = self.build_render_context(file_path, request, **kwargs)
        result, collector = self.render_with_static_assets(
            file_path,
            template,
            context_data,
            request=request,
        )
        if page_rendered.receivers:
            duration_ms = (time.perf_counter() - start) * 1000
            page_rendered.send(
                sender=Page,
                file_path=file_path,
                duration_ms=duration_ms,
                styles_count=len(collector.assets_in_slot("styles")),
                scripts_count=len(collector.assets_in_slot("scripts")),
                context_keys=tuple(context_data.keys()),
            )
        return result

    def _render_composed(
        self,
        file_path: Path,
        body: str,
        request: HttpRequest | None = None,
        **kwargs: object,
    ) -> str:
        """Compose `body` through layouts and render.

        The template-registry cache is bypassed so dynamic bodies
        produced by `render()` do not poison the cache.
        """
        start = time.perf_counter()
        composed = self._layout_manager._layout_loader.compose_body(body, file_path)
        return self._render_template_str(file_path, composed, start, request, **kwargs)

    def composed_template_for(self, file_path: Path) -> Template:
        """Return the compiled composed template for the static body.

        The composed source is cached in `_template_registry` and
        invalidated by source-mtime staleness. The compiled `Template`
        layer keys off the same registry, so both caches go stale
        together and a warm hit performs no file reads and no parsing.
        """
        if file_path not in self._template_registry or self._is_template_stale(
            file_path
        ):
            self._template_registry.pop(file_path, None)
            self._template_source_mtimes.pop(file_path, None)
            module = _load_python_module_memo(file_path)
            body = self._load_static_body(file_path, module)
            composed = self._layout_manager._layout_loader.compose_body(body, file_path)
            self.register_template(file_path, composed)
            self._record_template_source_mtimes(file_path)
        compiled = self._compiled_registry.get(file_path)
        if compiled is None:
            compiled = Template(self._template_registry[file_path])
            self._compiled_registry[file_path] = compiled
        return compiled

    def render(
        self,
        file_path: Path,
        request: HttpRequest | None = None,
        **kwargs: object,
    ) -> str:
        """Render the page with Django `Template` and the static collector.

        The static body source is the `template` attribute or any
        registered file-based `TemplateLoader`. The result is composed
        through the ancestor layout chain and cached compiled through
        `composed_template_for`. Direct callers of `Page.render` do not
        invoke `render()`. The unified view handles that path so
        dynamic bodies skip the registry cache.
        """
        start = time.perf_counter()
        template = self.composed_template_for(file_path)
        return self._render_template_str(file_path, template, start, request, **kwargs)

    def authorization_outcome(
        self,
        file_path: Path,
        request: HttpRequest,
        **kwargs: object,
    ) -> tuple[HttpResponseBase | None, bool]:
        """Re-run a page's body resolution once, reporting short-circuit and kind.

        The page module is loaded and its `render()` runs under the same
        dependency injection as the unified view, so guards, denials, and
        redirects fire exactly as they would on the page's own request. The
        first element is a short-circuit `HttpResponseBase` such as a
        redirect or a denial, or `None` when the body resolved to renderable
        content. The boolean is True when the body came from a `render()`
        string, which has no composed template to render a standalone zone
        against. An out-of-band zone morph reads both from one resolution so
        the foreign page's `render()` runs exactly once.
        """
        module = _load_python_module_memo(file_path)
        resolution = self._resolve_page_body(file_path, module, request, **kwargs)
        return resolution.http_response, resolution.dynamic

    def _create_unified_view(
        self,
        file_path: Path,
        _parameters: dict[str, str],
        module: types.ModuleType | None,
    ) -> Callable[..., HttpResponseBase]:
        """Return a view that resolves the body, composes layouts, and renders.

        The view carries `next_page_path` so the form dispatch can map a
        resolved origin URL back to the page source, including the
        synthesised `page.py` location of virtual `template.djx` pages.
        """

        def view(request: HttpRequest, **kwargs: object) -> HttpResponseBase:
            resolution = self._resolve_page_body(file_path, module, request, **kwargs)
            if resolution.http_response is not None:
                return resolution.http_response
            # next.partial imports next.pages, so the zone branch defers
            # its imports to break the cycle. Without the partial switch the
            # intent carries no zones and the full render path below runs
            # byte-for-byte the same as before.
            from next.partial import partial_intent  # noqa: PLC0415
            from next.partial.view import zone_response  # noqa: PLC0415

            intent = partial_intent(request)
            if intent.zones:
                return zone_response(
                    file_path,
                    intent,
                    request,
                    dynamic_body=resolution.dynamic,
                    url_kwargs=dict(kwargs),
                )
            body = resolution.body if resolution.body is not None else ""
            content = self._render_composed(file_path, body, request, **kwargs)
            return HttpResponse(content)

        cast("_RoutedPageView", view).next_page_path = file_path
        return view

    def has_template(
        self, file_path: Path, module: types.ModuleType | None = None
    ) -> bool:
        """Return whether any source can supply a template for this path."""
        if self._layout_manager._layout_loader.can_load(file_path):
            return True
        if module is not None and hasattr(module, "template"):
            return True
        return any(loader.can_load(file_path) for loader in build_registered_loaders())

    def _get_template_source_paths(self, file_path: Path) -> list[Path]:
        """Return file-based loader source files and layout files behind this page."""
        loader_paths: list[Path] = []
        for loader in build_registered_loaders():
            source = loader.source_path(file_path)
            if source is not None:
                loader_paths.append(source)
        layout_files = self._layout_manager._layout_loader._find_layout_files(file_path)
        return loader_paths + (layout_files or [])

    def _record_template_source_mtimes(self, file_path: Path) -> None:
        """Snapshot mtimes of template source files for stale detection."""
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
        """Return the URL pattern for a real `page.py` that has any body source."""
        module = _load_python_module_memo(file_path)
        if module is None:
            return None
        if not self._page_has_body_source(file_path, module):
            return None
        view = self._create_unified_view(file_path, parameters, module)
        return path(
            django_pattern,
            view,
            name=next_framework_settings.URL_NAME_TEMPLATE.format(name=clean_name),
        )

    def _create_virtual_page_pattern(
        self,
        file_path: Path,
        django_pattern: str,
        parameters: dict[str, str],
        clean_name: str,
    ) -> URLPattern | None:
        """Return the URL pattern for a template-only page without `page.py`."""
        if not self._page_has_body_source(file_path, module=None):
            return None
        view = self._create_unified_view(file_path, parameters, None)
        return path(
            django_pattern,
            view,
            name=next_framework_settings.URL_NAME_TEMPLATE.format(name=clean_name),
        )

    def _page_has_body_source(
        self,
        file_path: Path,
        module: types.ModuleType | None,
    ) -> bool:
        """Return True when `file_path` can produce a body or layout body."""
        if module is not None:
            if callable(getattr(module, "render", None)):
                return True
            if isinstance(getattr(module, "template", None), str):
                return True
        if any(loader.can_load(file_path) for loader in build_registered_loaders()):
            return True
        return self._layout_manager._layout_loader.can_load(file_path)

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
