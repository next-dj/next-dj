"""`Page` manager and its process-wide singleton.

`Page` orchestrates template loading, context collection, layout
composition, rendering, and URL-pattern wiring. `page` is the
application-wide singleton. `context` is a convenience alias for
`page.context` used by the `@context` decorator in user code.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast, overload

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.template import Context as DjangoTemplateContext, Origin, Template
from django.urls import URLPattern, path

from next.conf import fail_loudly, next_framework_settings
from next.deps import DependencyResolver, resolver
from next.ports import partial_shaper_slot
from next.utils import defining_file

from .loaders import (
    LayoutTemplateLoader,
    _load_python_module_memo,
    build_registered_loaders,
    has_load_errors,
    last_load_error,
)
from .paths import clear_page_path_info, forget_page_path_info, page_path_info
from .processors import _get_context_processors
from .registry import PageContextRegistry
from .signals import page_rendered, template_loaded


if TYPE_CHECKING:
    import types
    from collections.abc import Callable

    from next.static import StaticCollector
    from next.static.serializers import JsContextSerializer
    from next.urls import URLPatternParser

    from .registry import ZoneBinding


logger = logging.getLogger(__name__)


# Mtimes of every source behind one composition, keyed by its page path.
type _SourceMtimes = dict[Path, dict[Path, float]]


def template_edits_watched() -> bool:
    """Whether the composition caches stat their sources to notice an edit.

    Autoreload leaves `.djx` alone, so under `DEBUG` the stat is the only
    thing making an edit visible. Read per call, so an override takes effect.
    """
    return bool(settings.DEBUG)


class _RoutedPageView(Protocol):
    """A page view carrying the source path form dispatch resolves back to."""

    next_page_path: Path

    def __call__(self, request: HttpRequest, **kwargs) -> HttpResponseBase: ...


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
        """Initialise fresh registries and the layout loader.

        File-based template loaders are not held as an instance
        attribute. The module-level `build_registered_loaders()` helper
        caches them and invalidates on `settings_reloaded`.
        """
        self._template_registry: dict[Path, str] = {}
        self._compiled_registry: dict[Path, Template] = {}
        self._template_source_mtimes: _SourceMtimes = {}
        self._skeleton_registry: dict[Path, str] = {}
        self._skeleton_source_mtimes: _SourceMtimes = {}
        self._context_manager = PageContextRegistry(None)
        self._layout_loader = LayoutTemplateLoader()

    def _get_resolver(self) -> DependencyResolver:
        """Return the shared `resolver` singleton."""
        return resolver

    def register_template(self, file_path: Path, template_str: str) -> None:
        """Store rendered template source for `file_path`.

        The compiled-template entry and the memoised path facts go with it,
        so every layer keyed off the page path invalidates together.
        """
        self._template_registry[file_path] = template_str
        self._compiled_registry.pop(file_path, None)
        forget_page_path_info(file_path)
        template_loaded.send(sender=Page, file_path=file_path)

    def clear_template_caches(self) -> None:
        """Drop every composed layer, the mtime snapshots, and the path facts.

        A caller that rewrites a page or a layout in place inside one process
        needs this, because the composed source is memoised per page path and
        the staleness check only reruns when a recorded mtime moves.
        """
        self._template_registry.clear()
        self._compiled_registry.clear()
        self._template_source_mtimes.clear()
        self._skeleton_registry.clear()
        self._skeleton_source_mtimes.clear()
        clear_page_path_info()

    @overload
    def context[C: Callable[..., Any]](self, func_or_key: C, /) -> C: ...
    @overload
    def context[C: Callable[..., Any]](
        self,
        func_or_key: str | None = None,
        *,
        inherit_context: bool = False,
        serialize: bool = False,
        serializer: JsContextSerializer | None = None,
        zone: str | None = None,
    ) -> Callable[[C], C]: ...
    def context(
        self,
        func_or_key: Callable[..., Any] | str | None = None,
        *,
        inherit_context: bool = False,
        serialize: bool = False,
        serializer: JsContextSerializer | None = None,
        zone: str | None = None,
    ) -> Callable[..., Any]:
        """Register a keyed or dict-merge `@context` for the file declaring `func`.

        Pass `serialize=True` to include the return value in
        `Next.context` so JavaScript code on the page can read it via
        `window.Next.context`. Pass `serializer=` to route this key
        through a custom `JsContextSerializer` instead of the global
        `JS_CONTEXT_SERIALIZER` setting. Pass `zone=` to bind the
        callable to one zone, so a GET for another zone never runs it.
        """
        # Captured here rather than inside the decorator so both spellings see
        # the page.py that ran `@context`, not this module.
        registered_from = Path(sys._getframe(1).f_code.co_filename)

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            declared_in = defining_file(func)
            if declared_in != registered_from:
                self._context_manager.note_misattribution(
                    registered_from, declared_in, func
                )
            key = None if callable(func_or_key) else func_or_key
            self._context_manager.register_context(
                declared_in,
                key,
                func,
                inherit_context=inherit_context,
                serialize=serialize,
                serializer=serializer,
                zone=zone,
            )
            return func

        return decorator(func_or_key) if callable(func_or_key) else decorator

    def zone_bindings(self) -> dict[Path, tuple[ZoneBinding, ...]]:
        """Return the zone view of every registered `@context`, keyed by file."""
        return self._context_manager.zone_bindings()

    def build_render_context(
        self,
        file_path: Path,
        request: HttpRequest | None = None,
        *,
        _requested_zones: frozenset[str] | None = None,
        **kwargs,
    ) -> dict[str, object]:
        """Build the full render context dict used by `render`.

        The returned dict includes `_next_js_context` holding the subset
        of values marked `serialize=True`. `render` pops that key and
        seeds the `StaticCollector` with it before creating the Django
        template context. A `_requested_zones` batch narrows the page
        callables to that batch and stays out of the returned dict, so it
        never reaches a template or the JS context.
        """
        info = page_path_info(file_path)
        context_data: dict[str, object] = {
            "current_template_path": info.template_path,
            "current_page_module_path": info.module_path,
        }
        context_data.update(kwargs)

        context_result = self._context_manager.collect_context(
            file_path, request, _requested_zones=_requested_zones, **kwargs
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
            strict = next_framework_settings.STRICT_CONTEXT
            for processor in context_processors:
                try:
                    processor_data = processor(request)
                    if isinstance(processor_data, dict):
                        context_data.update(processor_data)
                except (TypeError, ValueError, AttributeError, KeyError) as e:
                    if strict:
                        raise
                    logger.warning(
                        "Error in context processor %s: %s", processor.__name__, e
                    )

        return context_data

    def _load_static_body(
        self, file_path: Path, module: types.ModuleType | None
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
        **kwargs,
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
        **kwargs,
    ) -> _BodyResolution:
        """Invoke `render_func` with DI-resolved arguments and classify the result."""
        dep_cache: dict[str, Any] = {}
        dep_stack: list[str] = []
        resolved = self._get_resolver().resolve_dependencies(
            render_func, request=request, _cache=dep_cache, _stack=dep_stack, **kwargs
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
        **kwargs,
    ) -> str:
        """Build context, render `template`, inject static assets, emit signal."""
        context_data = self.build_render_context(file_path, request, **kwargs)
        result, collector = self.render_with_static_assets(
            file_path, template, context_data, request=request
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
        self, file_path: Path, body: str, request: HttpRequest | None = None, **kwargs
    ) -> str:
        """Compose `body` through layouts and render.

        Only the layout skeleton is cached, so a dynamic body produced by
        `render()` never reaches the template registry.
        """
        start = time.perf_counter()
        skeleton = self._layout_skeleton_for(file_path)
        composed = self._layout_loader.fill_skeleton(skeleton, body)
        return self._render_template_str(file_path, composed, start, request, **kwargs)

    def _layout_skeleton_for(self, file_path: Path) -> str:
        """Return the cached layout chain of `file_path` with an empty body slot.

        The skeleton keeps its own mtime snapshot, because the composed
        registry refreshes its own on eviction and would hide a layout edit.
        """
        skeleton = self._skeleton_registry.get(file_path)
        if skeleton is None or self._is_template_stale(
            file_path, self._skeleton_source_mtimes
        ):
            skeleton = self._layout_loader.compose_skeleton(file_path)
            self._skeleton_registry[file_path] = skeleton
            self._skeleton_source_mtimes.pop(file_path, None)
            self._record_template_source_mtimes(file_path, self._skeleton_source_mtimes)
            # A dynamic page registers no template, so the facts drop here.
            forget_page_path_info(file_path)
        return skeleton

    def composed_template_for(self, file_path: Path) -> Template:
        """Return the compiled composed template for the static body.

        The composed source is cached in `_template_registry` and
        invalidated by source-mtime staleness. The compiled `Template`
        layer keys off the same registry, so both caches go stale
        together and a warm hit performs no file reads and no parsing.
        """
        if file_path not in self._template_registry or self._is_template_stale(
            file_path, self._template_source_mtimes
        ):
            self._template_registry.pop(file_path, None)
            self._template_source_mtimes.pop(file_path, None)
            module = _load_python_module_memo(file_path)
            body = self._load_static_body(file_path, module)
            composed = self._layout_loader.compose_body(body, file_path)
            self.register_template(file_path, composed)
            self._record_template_source_mtimes(file_path, self._template_source_mtimes)
        compiled = self._compiled_registry.get(file_path)
        if compiled is None:
            # The origin makes compile errors name the page path.
            compiled = Template(
                self._template_registry[file_path],
                origin=Origin(str(file_path)),
                name=str(file_path),
            )
            self._compiled_registry[file_path] = compiled
        return compiled

    def render(
        self, file_path: Path, request: HttpRequest | None = None, **kwargs
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
        self, file_path: Path, request: HttpRequest, **kwargs
    ) -> tuple[HttpResponseBase | None, bool]:
        """Resolve a page body once, reporting its short-circuit and its kind.

        `render()` runs under the same dependency injection as the unified
        view, so guards, denials, and redirects fire as they would on the
        page's own request. An out-of-band zone morph reads the response and
        the dynamic flag from this one resolution, so the foreign page's
        `render()` runs exactly once.
        """
        module = _load_python_module_memo(file_path)
        error = last_load_error(file_path)
        if error is not None:
            # Not Http404. A 404 would answer the caller's own URL instead of
            # the morph, and falling through would skip the page's guards.
            raise error
        resolution = self._resolve_page_body(file_path, module, request, **kwargs)
        return resolution.http_response, resolution.dynamic

    def _create_unified_view(
        self,
        file_path: Path,
        _parameters: dict[str, str],
        module: types.ModuleType | None,
        *,
        broken_at_build: bool = False,
    ) -> Callable[..., HttpResponseBase]:
        """Return the view for a page, on the branch its body source dictates.

        A page without a module-level `render()` serves one composed template,
        anything else resolves its body per request. The view carries
        `next_page_path` so form dispatch maps an origin URL back to the page.
        """
        has_render = module is not None and callable(getattr(module, "render", None))
        view = (
            self._create_resolving_view(
                file_path, module, broken_at_build=broken_at_build
            )
            if has_render or broken_at_build
            else self._create_static_view(file_path)
        )
        cast("_RoutedPageView", view).next_page_path = file_path
        return view

    def _create_static_view(self, file_path: Path) -> Callable[..., HttpResponseBase]:
        """Return the view of a page whose body comes from files on disk.

        Nothing about that body depends on the request, so the view serves
        the compiled composed template and resolves no body of its own.
        """

        def view(request: HttpRequest, **kwargs) -> HttpResponseBase:
            # `has_load_errors` first, so a healthy site pays no stat.
            if has_load_errors() and fail_loudly():
                error = last_load_error(file_path)
                if error is not None:
                    raise error
            shaper = partial_shaper_slot.get()
            intent = shaper.intent(request)
            if intent.zones:
                return shaper.zone_response(
                    file_path,
                    request,
                    intent,
                    dynamic_body=False,
                    url_kwargs=dict(kwargs),
                )
            return HttpResponse(self.render(file_path, request, **kwargs))

        return view

    def _create_resolving_view(
        self, file_path: Path, module: types.ModuleType | None, *, broken_at_build: bool
    ) -> Callable[..., HttpResponseBase]:
        """Return the view that resolves a per-request body before composing.

        A page marked `broken_at_build` re-reads its module per request, so a
        fixed file comes back without a restart and a still-broken one never
        slips past the guards its `render()` would have applied.
        """

        def view(request: HttpRequest, **kwargs) -> HttpResponseBase:
            active_module = module
            if broken_at_build:
                # The memo re-reads by mtime and drops the recorded error
                # once the file imports cleanly again.
                active_module = _load_python_module_memo(file_path)
                error = last_load_error(file_path)
                if error is not None:
                    if fail_loudly():
                        raise error
                    raise Http404
            # Both guards first, so a healthy site pays no stat.
            elif has_load_errors() and fail_loudly():
                error = last_load_error(file_path)
                if error is not None:
                    raise error
            resolution = self._resolve_page_body(
                file_path, active_module, request, **kwargs
            )
            if resolution.http_response is not None:
                return resolution.http_response
            shaper = partial_shaper_slot.get()
            intent = shaper.intent(request)
            if intent.zones:
                return shaper.zone_response(
                    file_path,
                    request,
                    intent,
                    dynamic_body=resolution.dynamic,
                    url_kwargs=dict(kwargs),
                )
            body = resolution.body if resolution.body is not None else ""
            content = self._render_composed(file_path, body, request, **kwargs)
            return HttpResponse(content)

        return view

    def has_template(
        self, file_path: Path, module: types.ModuleType | None = None
    ) -> bool:
        """Return whether any source can supply a template for this path."""
        if self._layout_loader.can_load(file_path):
            return True
        if module is not None and hasattr(module, "template"):
            return True
        return any(loader.can_load(file_path) for loader in build_registered_loaders())

    def _get_template_source_paths(self, file_path: Path) -> list[Path]:
        """Return every path whose change alters the composition of this page.

        The directories come along with the files, because a `layout.djx`
        created or deleted there moves no mtime a file-only snapshot sees.
        """
        paths: list[Path] = []
        for loader in build_registered_loaders():
            source = loader.source_path(file_path)
            if source is not None:
                paths.append(source)
        layout_files, watched_dirs = self._layout_loader.layout_sources(file_path)
        paths += layout_files
        paths += watched_dirs
        return paths

    def _record_template_source_mtimes(
        self, file_path: Path, store: _SourceMtimes
    ) -> None:
        """Snapshot mtimes of the template sources of `file_path` into `store`.

        Taken whether or not the process watches edits, because an entry
        composed with the watch off would hold nothing to compare against.
        """
        paths = self._get_template_source_paths(file_path)
        if not paths:
            return
        mtimes: dict[Path, float] = {}
        for p in paths:
            with contextlib.suppress(OSError):
                mtimes[p] = p.stat().st_mtime
        if mtimes:
            store[file_path] = mtimes

    def _is_template_stale(self, file_path: Path, store: _SourceMtimes) -> bool:
        """Return whether any source tracked in `store` changed on disk.

        A source that no longer stats reads as changed, and a process
        watching no edit answers no without a single stat.
        """
        if not template_edits_watched():
            return False
        stored = store.get(file_path)
        if not stored:
            return False
        for p, old_mtime in stored.items():
            try:
                current = p.stat().st_mtime
            except OSError:
                return True
            if current > old_mtime:
                return True
        return False

    def _create_regular_page_pattern(
        self,
        file_path: Path,
        django_pattern: str,
        parameters: dict[str, str],
        clean_name: str,
    ) -> URLPattern | None:
        """Return the URL pattern for a real `page.py` that has any body source.

        A `page.py` whose import failed still gets a pattern so the view can
        surface the recorded error per request without touching siblings.
        """
        module = _load_python_module_memo(file_path)
        broken_at_build = False
        if module is None:
            if last_load_error(file_path) is None:
                return None
            broken_at_build = True
        elif not self._page_has_body_source(file_path, module):
            return None
        view = self._create_unified_view(
            file_path, parameters, module, broken_at_build=broken_at_build
        )
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
        self, file_path: Path, module: types.ModuleType | None
    ) -> bool:
        """Return True when `file_path` can produce a body or layout body."""
        if module is not None:
            if callable(getattr(module, "render", None)):
                return True
            if isinstance(getattr(module, "template", None), str):
                return True
        if any(loader.can_load(file_path) for loader in build_registered_loaders()):
            return True
        return self._layout_loader.can_load(file_path)

    def create_url_pattern(
        self, url_path: str, file_path: Path, url_parser: URLPatternParser
    ) -> URLPattern | None:
        """Return a `path()` pattern for a page, template, or virtual entry."""
        # The error class comes through the parser, because importing next.urls
        # at module level would close the next.pages <-> next.urls cycle.
        try:
            django_pattern, parameters = url_parser.parse_url_pattern(url_path)
        except url_parser.duplicate_parameter_error as exc:
            raise url_parser.duplicate_parameter_error(
                exc.param_name, exc.url_path, file_path=file_path
            ) from exc
        clean_name = url_parser.prepare_url_name(url_path)

        if file_path.exists():
            return self._create_regular_page_pattern(
                file_path, django_pattern, parameters, clean_name
            )
        return self._create_virtual_page_pattern(
            file_path, django_pattern, parameters, clean_name
        )


page: Page = Page()
context = page.context


def reset_context_registry() -> None:
    """Clear the shared page-context registry for a from-disk rebuild.

    The check-cache reset pairs this with the module memo so a re-executed
    `page.py` repopulates the registry from its current source.
    """
    page._context_manager.reset()
