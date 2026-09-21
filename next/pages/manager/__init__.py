"""`Page` manager and its process-wide singleton.

`context` is the alias for `page.context` that user code spells as `@context`.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, overload

from django.http.response import HttpResponseBase
from django.template import Context as DjangoTemplateContext, Origin, Template

from next.conf import next_framework_settings
from next.deps.resolver import current_resolver
from next.introspect import defining_file
from next.pages.loaders import (
    LayoutTemplateLoader,
    _load_python_module_memo,
    build_registered_loaders,
    last_load_error,
)
from next.pages.paths import clear_page_path_info, forget_page_path_info, page_path_info
from next.pages.processors import _get_context_processors
from next.pages.registry import PageContextRegistry
from next.pages.signals import page_rendered, template_loaded
from next.pages.visits import visit_request
from next.ports import static_assets_slot
from next.seeding import (
    JS_CONTEXT_KEY,
    JS_SERIALIZERS_KEY,
    PAGE_MODULE_PATH_KEY,
    REQUEST_KEY,
    TEMPLATE_PATH_KEY,
    seed_collector,
)

from .templates import PageTemplateCache
from .views import create_url_pattern, unified_view


if TYPE_CHECKING:
    import types
    from collections.abc import Callable, Mapping

    from django.http import HttpRequest
    from django.urls import URLPattern

    from next.pages.registry import ZoneBinding
    from next.static import StaticCollector
    from next.static.serializers import JsContextSerializer
    from next.urls import URLPatternParser


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _BodyResolution:
    """Per-request outcome of `Page._resolve_page_body`.

    `http_response` is typed `HttpResponseBase` so streaming responses flow through
    verbatim, and a string `dynamic` body has no compiled source for a standalone zone.
    """

    body: str | None = None
    http_response: HttpResponseBase | None = None
    dynamic: bool = False


class Page:
    """Coordinate template loading, context, layouts, rendering, and URL wiring."""

    def __init__(self) -> None:
        """Initialise fresh registries and the layout loader.

        File-based loaders live in the module-level `build_registered_loaders()` cache.
        """
        self._layout_loader = LayoutTemplateLoader()
        self._templates = PageTemplateCache(self._layout_loader)
        self._context_manager = PageContextRegistry()

    def register_template(self, file_path: Path, template_str: str) -> None:
        """Store rendered template source for `file_path`.

        The compiled-template entry and the memoised path facts go with it,
        so every layer keyed off the page path invalidates together.
        """
        self._templates.composed[file_path] = template_str
        self._templates.compiled.pop(file_path)
        forget_page_path_info(file_path)
        template_loaded.send(sender=Page, file_path=file_path)

    def clear_template_caches(self) -> None:
        """Drop every composed layer, the mtime snapshots, and the path facts.

        For a page or layout rewritten in place, since staleness needs a moved mtime.
        """
        self._templates.clear()
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

        `serialize=True` publishes the value on `window.Next.context`, `serializer=`
        overrides `JS_CONTEXT_SERIALIZER`, and `zone=` binds the callable to one zone.
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

        `JS_CONTEXT_KEY` carries the `serialize=True` subset, which `render` pops for
        the `StaticCollector`. A `_requested_zones` batch stays out of the dict.
        """
        info = page_path_info(file_path)
        context_data: dict[str, object] = {
            TEMPLATE_PATH_KEY: info.template_path,
            PAGE_MODULE_PATH_KEY: info.module_path,
        }
        context_data.update(kwargs)

        context_result = self._context_manager.collect_context(
            file_path, request, _requested_zones=_requested_zones, **kwargs
        )
        context_data.update(context_result.context_data)
        context_data[JS_CONTEXT_KEY] = context_result.js_context
        context_data[JS_SERIALIZERS_KEY] = context_result.js_context_serializers

        if request is not None:
            context_data[REQUEST_KEY] = request

        context_processors = _get_context_processors()
        if request is not None and context_processors:
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

        No source becomes empty, so an ancestor layout still has a slot to fill.
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

        The order is `render()`, the `template` attribute, the loader chain, then empty.
        An `HttpResponseBase` from `render()` bypasses the layout and static pipelines.
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
        resolved = current_resolver().resolve_dependencies(
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

        `template` is either a precompiled `Template` or raw source parsed here. The
        collector comes back with the HTML, so telemetry needs no second render.
        """
        collector = seed_collector(file_path, context_data)
        compiled = template if isinstance(template, Template) else Template(template)
        html = compiled.render(DjangoTemplateContext(context_data))
        assets = static_assets_slot.get()
        result = assets.inject(html, collector, page_path=file_path, request=request)
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
        if page_rendered.has_listeners(Page):
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
        """Return the cached layout chain of `file_path` with an empty body slot."""
        templates = self._templates
        skeleton = templates.skeleton.get(file_path)
        if skeleton is None or templates.skeleton_is_stale(file_path):
            skeleton = self._layout_loader.compose_skeleton(file_path)
            templates.skeleton[file_path] = skeleton
            templates.record_skeleton(file_path)
            # A dynamic page registers no template, so the facts drop here.
            forget_page_path_info(file_path)
        return skeleton

    def composed_template_for(self, file_path: Path) -> Template:
        """Return the compiled composed template for the static body.

        Both caches key off the composed layer and go stale together. The source is
        held in a local, because the bound may evict it between two reads.
        """
        templates = self._templates
        composed = templates.composed.get(file_path)
        if composed is None or templates.composed_is_stale(file_path):
            templates.forget_composed(file_path)
            module = _load_python_module_memo(file_path)
            body = self._load_static_body(file_path, module)
            composed = self._layout_loader.compose_body(body, file_path)
            self.register_template(file_path, composed)
            templates.record_composed(file_path)
        compiled = templates.compiled.get(file_path)
        if compiled is None:
            # The origin makes compile errors name the page path.
            compiled = Template(
                composed, origin=Origin(str(file_path)), name=str(file_path)
            )
            templates.compiled[file_path] = compiled
        return compiled

    def render(
        self, file_path: Path, request: HttpRequest | None = None, **kwargs
    ) -> str:
        """Render the page with Django `Template` and the static collector.

        The body comes from the `template` attribute or a file-based loader and is
        composed through the layout chain, so a direct caller never invokes `render()`.
        """
        start = time.perf_counter()
        template = self.composed_template_for(file_path)
        return self._render_template_str(file_path, template, start, request, **kwargs)

    def authorization_outcome(
        self,
        file_path: Path,
        request: HttpRequest,
        visit_url: str | None,
        url_kwargs: Mapping[str, object] | None = None,
    ) -> tuple[HttpResponseBase | None, bool]:
        """Resolve a page body once, reporting its short-circuit and its kind.

        `render()` runs under the same injection as the unified view, against a request
        presenting a GET visit of `visit_url`, so a guard keyed on the shape of the
        request answers as it would on a visit. A caller that knows no URL for the page
        passes `None` and leaves the live path in place. A page without a `render()`
        authorizes every caller, exactly as its own static view does, and loads no body.
        """
        module = _load_python_module_memo(file_path)
        error = last_load_error(file_path)
        if error is not None:
            # Not Http404. A 404 would answer the caller's own URL instead of
            # the morph, and falling through would skip the page's guards.
            raise error
        render_func = getattr(module, "render", None) if module is not None else None
        if not callable(render_func):
            return None, False
        resolution = self._call_render_function(
            render_func,
            file_path,
            visit_request(request, visit_url),
            **dict(url_kwargs or {}),
        )
        return resolution.http_response, resolution.dynamic

    def has_template(
        self, file_path: Path, module: types.ModuleType | None = None
    ) -> bool:
        """Return whether any source can supply a template for this path."""
        if self._layout_loader.can_load(file_path):
            return True
        if module is not None and hasattr(module, "template"):
            return True
        return any(loader.can_load(file_path) for loader in build_registered_loaders())

    def _create_unified_view(
        self,
        file_path: Path,
        module: types.ModuleType | None,
        *,
        broken_at_build: bool = False,
    ) -> Callable[..., HttpResponseBase]:
        """Return the view a route serves this page through."""
        return unified_view(self, file_path, module, broken_at_build=broken_at_build)

    def create_url_pattern(
        self, url_path: str, file_path: Path, url_parser: URLPatternParser
    ) -> URLPattern | None:
        """Return a `path()` pattern for a page, template, or virtual entry."""
        return create_url_pattern(self, url_path, file_path, url_parser)


page: Page = Page()
context = page.context


def reset_context_registry() -> None:
    """Clear the shared page-context registry for a from-disk rebuild.

    The check-cache reset pairs this with the module memo so a re-executed
    `page.py` repopulates the registry from its current source.
    """
    page._context_manager.reset()


__all__ = ["Page", "context", "page", "reset_context_registry"]
