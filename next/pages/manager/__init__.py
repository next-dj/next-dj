"""The `Page` manager and its process-wide singleton.

`context` is an alias of `page.context`, which user code applies as `@context`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, Any, NamedTuple, cast, overload

from django.conf import settings
from django.http.response import HttpResponseBase
from django.template import Context as DjangoTemplateContext, Origin, Template

from next.boundaries import protect
from next.caches import PageCache
from next.components.registry import deferred_components
from next.conf import next_framework_settings
from next.deps.cache import DependencyCache, render_dep_cache
from next.deps.context import RESERVED_KEYS, ResolutionContext
from next.deps.resolver import current_resolver
from next.deps.runner import run_on_request_thread, run_segment
from next.introspect import declared_file, is_coroutine_callable, registering_file
from next.pages.access import PageDeclarations
from next.pages.boundaries import FAILURE_KEY, Caught, Shell, boundary_scope, catching
from next.pages.compression import CompressionDeclarations, compress_declarations
from next.pages.deferral import BoundaryDeclarations, page_defer_declarations
from next.pages.gating import GATE_KEY, Gate, Plan, note_slow, plan_gating
from next.pages.graph import (
    Phase,
    arun_phase,
    blaming,
    callable_keys,
    merged,
    run_phase,
)
from next.pages.loaders import (
    BoundaryChain,
    LayoutTemplateLoader,
    build_registered_loaders,
    forget_layout_texts,
    load_page_module,
    load_python_module_memo,
    page_boundaries,
    reset_module_memo,
)
from next.pages.metadata.chain import (
    ChainEntry,
    FoldedMetadata,
    MetadataDeclaration,
    MetadataThunk,
    StaticMetadata,
    chain_entry,
    contained_chain,
    declared_metadata,
    fold_chain,
    static_metadata,
)
from next.pages.metadata.registry import MetadataRegistrations, PageMetadataRegistry
from next.pages.metadata.scope import forget_metadata_scope
from next.pages.navigation import NavigationDeclarations, router_declarations
from next.pages.paths import clear_page_path_info, forget_page_path_info, page_path_info
from next.pages.processors import _get_context_processors
from next.pages.registry import PageContextRegistry
from next.pages.responses import depth_of, purpose_of
from next.pages.scope import render_scope
from next.pages.signals import page_rendered, template_loaded
from next.pages.transactions import TransactionDeclarations, atomic_declarations
from next.pages.visits import visit_request
from next.ports import render_deferred, run_bridged, static_assets_slot
from next.seeding import (
    DEP_CACHE_KEY,
    JS_CONTEXT_KEY,
    JS_SERIALIZERS_KEY,
    METADATA_KEY,
    PAGE_MODULE_PATH_KEY,
    REQUEST_KEY,
    TEMPLATE_PATH_KEY,
    seed_collector,
)

from .patterns import create_url_pattern
from .templates import PageTemplateCache


if TYPE_CHECKING:
    import types
    from collections.abc import Awaitable, Callable, Mapping, MutableMapping
    from pathlib import Path

    from django.http import HttpRequest
    from django.urls import URLPattern

    from next.pages.context import ContextResult
    from next.pages.graph import Blame
    from next.pages.metadata import Metadata
    from next.pages.metadata.markers import Segment
    from next.pages.registry import ZoneBinding
    from next.static import StaticCollector
    from next.static.serializers import JsContextSerializer
    from next.urls import URLPatternParser


logger = logging.getLogger(__name__)


def _resolution(
    request: HttpRequest | None,
    url_kwargs: Mapping[str, object],
    dep_cache: dict[str, Any],
) -> ResolutionContext:
    """Return the context the async path resolves a page callable against."""
    return ResolutionContext(
        request=request,
        form=None,
        url_kwargs={
            name: value
            for name, value in url_kwargs.items()
            if name not in RESERVED_KEYS
        },
        context_data={},
        cache=DependencyCache(dep_cache),
    )


def _run_processors(
    processors: list[Callable[[Any], dict[str, Any]]],
    context_data: dict[str, object],
    request: HttpRequest,
) -> None:
    """Merge what each context processor returns, logging a failure unless strict."""
    strict = next_framework_settings.STRICT_CONTEXT
    for processor in processors:
        try:
            processor_data = processor(request)
            if isinstance(processor_data, dict):
                context_data.update(processor_data)
        except (TypeError, ValueError, AttributeError, KeyError) as e:
            if strict:
                raise
            logger.warning("Error in context processor %s: %s", processor.__name__, e)


def _finish_context(
    context_data: dict[str, object],
    request: HttpRequest | None,
    registry: PageMetadataRegistry | None,
    file_path: Path,
) -> ChainEntry | None:
    """Run the context processors, then return the metadata chain when `registry`."""
    context_processors = _get_context_processors()
    if request is not None and context_processors:
        _run_processors(context_processors, context_data, request)
    return None if registry is None else contained_chain(registry, file_path)


@dataclass(frozen=True, slots=True)
class _BodyResolution:
    """The per-request outcome of `Page.resolve_page_body`.

    `http_response` may stream, and a `dynamic` body has no source to render a zone.
    """

    body: str | None = None
    http_response: HttpResponseBase | None = None
    dynamic: bool = False


class Built(NamedTuple):
    """A render context built ahead of its template render and when the build began."""

    context: dict[str, object]
    started: float


def defers(chain: BoundaryChain | None = None) -> bool:
    """Return whether a render may queue a deferred call, so it opens a gathering block.

    A page boundary with a `loading.djx` in `chain` or any deferrable component does.
    """
    return deferred_components.held or (chain is not None and bool(chain.deferred))


def _classified(result: object, file_path: Path) -> _BodyResolution:
    """Classify what a page `render()` returned, refusing anything but a body."""
    if isinstance(result, HttpResponseBase):
        return _BodyResolution(http_response=result)
    if isinstance(result, str):
        return _BodyResolution(body=result, dynamic=True)
    msg = (
        f"page.py render() at {file_path} must return str or "
        f"HttpResponseBase, got {type(result).__name__}."
    )
    raise TypeError(msg)


class Page(
    PageDeclarations,
    TransactionDeclarations,
    BoundaryDeclarations,
    CompressionDeclarations,
    NavigationDeclarations,
):
    """Coordinate template loading, context, layouts, rendering, and URL wiring.

    Its access declarations bind its own page, and the pages below under `inherit=True`.
    """

    def __init__(self) -> None:
        """Initialise fresh registries and the layout loader.

        File-based loaders live in the module-level `build_registered_loaders()` cache.
        """
        self._layout_loader = LayoutTemplateLoader()
        self._templates = PageTemplateCache(self._layout_loader)
        self.epoch = 0
        """The counter a write to the context or the metadata registry moves."""
        self._colours: PageCache[Path, tuple[int, bool]] = PageCache()
        self._plans: PageCache[Path, tuple[object, int, object, Plan]] = PageCache()
        self._context_manager = PageContextRegistry(on_change=self._recolour)
        self._metadata_registry = PageMetadataRegistry(on_change=self._recolour)

    def _recolour(self) -> None:
        """Move the epoch the memoised colours carry, after a write to a registry."""
        self.epoch += 1

    @property
    def contexts(self) -> PageContextRegistry:
        """Return the registry of every `@context`, read by the checks and the tools."""
        return self._context_manager

    def register_template(self, file_path: Path, template_str: str) -> None:
        """Store rendered template source for `file_path`.

        The compiled template and path facts go with it, invalidating every layer.
        """
        self._templates.composed[file_path] = template_str
        self._templates.compiled.pop(file_path)
        self._templates.composed_chains.pop(file_path)
        forget_page_path_info(file_path)
        template_loaded.send(sender=Page, file_path=file_path)

    def clear_template_caches(self) -> None:
        """Drop every composed layer, the mtime snapshots, and the path facts.

        Used for a file rewritten in place, since stale detection needs a changed mtime.
        """
        self._templates.clear()
        forget_layout_texts()
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
        guard: bool | None = None,
    ) -> Callable[[C], C]: ...
    def context(
        self,
        func_or_key: Callable[..., Any] | str | None = None,
        *,
        inherit_context: bool = False,
        serialize: bool = False,
        serializer: JsContextSerializer | None = None,
        zone: str | None = None,
        guard: bool | None = None,
    ) -> Callable[..., Any]:
        """Register a keyed or dict-merge `@context` for the file declaring `func`.

        `serialize=True` publishes, `zone=` binds a zone, `guard=` runs before a shell.
        """
        registered_from = registering_file()

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            declared_in = declared_file(
                func, registered_from, self._context_manager.note_misattribution
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
            if guard is not None:
                self._context_manager.declare_guard(declared_in, key, guard=guard)
            return func

        return decorator(func_or_key) if callable(func_or_key) else decorator

    def zone_bindings(self) -> dict[Path, tuple[ZoneBinding, ...]]:
        """Return the zone view of every registered `@context`, keyed by file."""
        return self._context_manager.zone_bindings()

    @overload
    def metadata[C: Callable[..., Any]](self, func: C, /) -> C: ...
    @overload
    def metadata[C: Callable[..., Any]](
        self, /, *, inherit: bool = False
    ) -> Callable[[C], C]: ...
    def metadata(
        self, func: Callable[..., Any] | None = None, /, *, inherit: bool = False
    ) -> Callable[..., Any]:
        """Register the dynamic metadata callable of the `page.py` declaring `func`.

        `inherit=True` runs it for the descendant pages too, ahead of their own.
        """
        registered_from = registering_file()
        registry = self._metadata_registry

        def decorator(target: Callable[..., Any]) -> Callable[..., Any]:
            declared_in = declared_file(
                target, registered_from, registry.note_misattribution
            )
            registry.register(declared_in, target, inherit=inherit)
            return target

        return decorator if func is None else decorator(func)

    def metadata_declaration(self, file_path: Path) -> MetadataDeclaration:
        """Return what the `page.py` at `file_path` declares, dict or callable."""
        return declared_metadata(self._metadata_registry, file_path)

    def metadata_chain(self, file_path: Path) -> ChainEntry:
        """Return the memoised metadata chain of `file_path`."""
        return chain_entry(self._metadata_registry, file_path)

    def reset_metadata(self) -> None:
        """Drop every metadata registration and the chain memo built from them."""
        self._metadata_registry.reset()

    def metadata_registrations(self) -> MetadataRegistrations:
        """Return the registered callables and their misattributions."""
        return self._metadata_registry.registrations()

    def static_metadata(self, file_path: Path) -> StaticMetadata:
        """Return the metadata of `file_path` readable without a request.

        A chain the schema refuses reads as site defaults under `noindex`.
        """
        return static_metadata(self._metadata_registry, file_path)

    def fold_metadata(
        self,
        file_path: Path,
        *,
        overlay: Segment | None = None,
        request: HttpRequest | None = None,
        url_kwargs: Mapping[str, object] | None = None,
        context_data: Callable[[], MutableMapping[str, object]] | None = None,
    ) -> Metadata:
        """Fold the chain of `file_path`, `overlay` standing in for its own callable.

        `context_data` is called only when an inherited callable of an ancestor runs.
        """
        return fold_chain(
            self._metadata_registry,
            file_path,
            dep_cache=render_dep_cache(request),
            overlay=overlay,
            request=request,
            url_kwargs=url_kwargs,
            context_data=context_data,
        )

    def awaits(self, file_path: Path) -> bool:
        """Return whether a render of `file_path` runs async context or metadata.

        Memoised against the epoch both registries move, the read a sync render pays.
        """
        epoch = self.epoch
        held = self._colours.get(file_path)
        if held is not None and held[0] == epoch:
            return held[1]
        found = self._context_manager.awaits(
            file_path
        ) or self._metadata_registry.awaits(file_path)
        self._colours[file_path] = (epoch, found)
        return found

    def build_render_context(
        self,
        file_path: Path,
        request: HttpRequest | None = None,
        url_kwargs: Mapping[str, object] | None = None,
        *,
        requested_zones: frozenset[str] | None = None,
    ) -> dict[str, object]:
        """Build the full render context dict used by `render`.

        An async page runs through the bridge the server binds, in a scope of its own.
        """
        dep_cache = render_dep_cache(request)
        kwargs = url_kwargs or {}
        if self.awaits(file_path):
            return run_bridged(
                partial(
                    self._scoped_context,
                    file_path,
                    request,
                    kwargs,
                    requested_zones,
                    dep_cache,
                )
            )
        return self._render_context(
            file_path, request, kwargs, requested_zones, dep_cache
        )

    async def _scoped_context(
        self,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        requested_zones: frozenset[str] | None,
        dep_cache: dict[str, Any],
    ) -> dict[str, object]:
        """Build the render context of an async page inside its own request scope."""
        async with render_scope(dep_cache):
            return await self.arender_context(
                file_path,
                request,
                url_kwargs,
                dep_cache=dep_cache,
                requested_zones=requested_zones,
            )

    async def arender_context(
        self,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        *,
        dep_cache: dict[str, Any],
        requested_zones: frozenset[str] | None = None,
    ) -> dict[str, object]:
        """Build what `build_render_context` builds, awaiting the async page callables.

        The processors run in one blocking segment, and async metadata folds after them.
        """
        info = page_path_info(file_path)
        context_data: dict[str, object] = {
            TEMPLATE_PATH_KEY: info.template_path,
            PAGE_MODULE_PATH_KEY: info.module_path,
        }
        context_data.update(url_kwargs)
        context_result = await self._context_manager.acollect_context(
            file_path,
            request,
            url_kwargs,
            dep_cache=dep_cache,
            requested_zones=requested_zones,
        )
        context_data.update(context_result.context_data)
        context_data[JS_CONTEXT_KEY] = context_result.js_context
        context_data[JS_SERIALIZERS_KEY] = context_result.js_context_serializers
        thunk = MetadataThunk(
            self._metadata_registry, file_path, request, url_kwargs, dep_cache
        )
        context_data[METADATA_KEY] = thunk
        context_data[DEP_CACHE_KEY] = dep_cache
        if request is not None:
            context_data[REQUEST_KEY] = request
        registry = (
            self._metadata_registry
            if self._metadata_registry.awaits(file_path)
            else None
        )
        if registry is None and (request is None or not _get_context_processors()):
            return context_data
        entry = await run_segment(
            partial(_finish_context, context_data, request, registry, file_path)
        )
        if entry is not None and entry.folded is None:
            folded = await thunk.afold(entry, dict(context_data))
            context_data[METADATA_KEY] = FoldedMetadata(thunk, folded)
        return context_data

    def _render_context(
        self,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        requested_zones: frozenset[str] | None,
        dep_cache: dict[str, Any],
    ) -> dict[str, object]:
        """Build the render context, resolving through the cache `render()` used."""
        info = page_path_info(file_path)
        context_data: dict[str, object] = {
            TEMPLATE_PATH_KEY: info.template_path,
            PAGE_MODULE_PATH_KEY: info.module_path,
        }
        context_data.update(url_kwargs)

        context_result = self._context_manager.collect_context(
            file_path,
            request,
            url_kwargs,
            dep_cache=dep_cache,
            requested_zones=requested_zones,
        )
        context_data.update(context_result.context_data)
        context_data[JS_CONTEXT_KEY] = context_result.js_context
        context_data[JS_SERIALIZERS_KEY] = context_result.js_context_serializers
        context_data[METADATA_KEY] = MetadataThunk(
            self._metadata_registry, file_path, request, url_kwargs, dep_cache
        )
        context_data[DEP_CACHE_KEY] = dep_cache

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

    def resolve_page_body(
        self,
        file_path: Path,
        module: types.ModuleType | None,
        request: HttpRequest | None = None,
        url_kwargs: Mapping[str, object] | None = None,
        *,
        dep_cache: dict[str, Any],
    ) -> _BodyResolution:
        """Resolve the page body per-request.

        An `HttpResponseBase` from `render()` bypasses the layout and static pipelines.
        """
        if module is not None:
            render_func = getattr(module, "render", None)
            if callable(render_func):
                return self._call_render_function(
                    render_func, file_path, request, url_kwargs or {}, dep_cache
                )
        return _BodyResolution(body=self._load_static_body(file_path, module))

    async def aresolve_page_body(
        self,
        file_path: Path,
        module: types.ModuleType | None,
        request: HttpRequest | None = None,
        url_kwargs: Mapping[str, object] | None = None,
        *,
        dep_cache: dict[str, Any],
    ) -> _BodyResolution:
        """Resolve the page body on the async path, awaiting an `async def render`.

        Any other body resolves in one blocking segment, as `resolve_page_body` does.
        """
        render_func = None if module is None else getattr(module, "render", None)
        if not is_coroutine_callable(render_func):
            return await run_segment(
                partial(
                    self.resolve_page_body,
                    file_path,
                    module,
                    request,
                    url_kwargs,
                    dep_cache=dep_cache,
                )
            )
        context = _resolution(request, url_kwargs or {}, dep_cache)
        result = await current_resolver().acall(render_func, context)
        return _classified(result, file_path)

    async def _authorize(
        self,
        render_func: Callable[..., object],
        file_path: Path,
        request: HttpRequest,
        url_kwargs: Mapping[str, object],
    ) -> _BodyResolution:
        """Await an `async def render` for an authorization, in a scope of its own."""
        async with render_scope({}):
            context = _resolution(request, url_kwargs, {})
            result = await current_resolver().acall(render_func, context)
        return _classified(result, file_path)

    def _call_render_function(
        self,
        render_func: Callable[..., object],
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        dep_cache: dict[str, Any],
    ) -> _BodyResolution:
        """Invoke `render_func` with DI-resolved arguments and classify the result."""
        resolved = current_resolver().resolve_dependencies(
            render_func, request=request, _cache=dep_cache, _stack=[], **url_kwargs
        )
        return _classified(render_func(**resolved), file_path)

    def render_with_static_assets(
        self,
        file_path: Path,
        template: Template | str,
        context_data: dict[str, object],
        *,
        request: HttpRequest | None = None,
        deferred: bool = False,
    ) -> tuple[str, StaticCollector]:
        """Render `template` and inject collected static assets.

        Deferred calls settle first, so the assets their jobs add are injected too.
        """
        collector = seed_collector(file_path, context_data)
        compiled = template if isinstance(template, Template) else Template(template)
        context = DjangoTemplateContext(context_data)
        if deferred or deferred_components.held:
            layer = context.update({})
            html = render_deferred(
                partial(compiled.render, context),
                collector=collector,
                request=request,
                context=layer,
            )
        else:
            html = compiled.render(context)
        assets = static_assets_slot.get()
        result = assets.inject(html, collector, page_path=file_path, request=request)
        return result, collector

    def _render_template_str(
        self,
        file_path: Path,
        template: Template | str,
        start: float,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        *,
        dep_cache: dict[str, Any] | None,
        basis: dict[str, object] | BoundaryChain,
    ) -> str:
        """Render over the context `basis` holds, else one built inside its chain.

        An async page builds it through the bridge, so a sync view needs no colour.
        """
        if isinstance(basis, BoundaryChain):
            chain = basis
            if dep_cache is None:
                dep_cache = render_dep_cache(request)
            built = (
                self.gated_context(
                    file_path, request, url_kwargs, dep_cache=dep_cache, chain=chain
                )
                if chain.deferred
                else self.guarded_context(
                    file_path, request, url_kwargs, dep_cache=dep_cache, chain=chain
                )
            )
            if isinstance(built, Caught):
                return self.render_caught(
                    file_path,
                    built,
                    request,
                    url_kwargs,
                    dep_cache=dep_cache,
                    started=start,
                    chain=chain,
                )
            context_data = built
        else:
            context_data = basis
        result, collector = self.render_with_static_assets(
            file_path,
            template,
            context_data,
            request=request,
            deferred=GATE_KEY in context_data,
        )
        if page_rendered.has_listeners(Page):
            self._announce(file_path, start, request, collector, context_data)
        return result

    def _announce(
        self,
        file_path: Path,
        start: float,
        request: HttpRequest | None,
        collector: StaticCollector,
        context_data: dict[str, object],
    ) -> None:
        """Send `page_rendered` for a render that began at `start`."""
        page_rendered.send(
            sender=Page,
            file_path=file_path,
            duration_ms=(time.perf_counter() - start) * 1000,
            styles_count=len(collector.assets_in_slot("styles")),
            scripts_count=len(collector.assets_in_slot("scripts")),
            context_keys=tuple(context_data.keys()),
            purpose=purpose_of(request),
            depth=depth_of(request),
        )

    def _built_context(
        self,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        dep_cache: dict[str, Any],
        requested_zones: frozenset[str] | None = None,
    ) -> dict[str, object]:
        """Build the full render context, an async page through the bridge."""
        if self.awaits(file_path):
            return run_bridged(
                partial(
                    self._scoped_context,
                    file_path,
                    request,
                    url_kwargs,
                    requested_zones,
                    dep_cache,
                )
            )
        return self._render_context(
            file_path, request, url_kwargs, requested_zones, dep_cache
        )

    def guarded_context(
        self,
        file_path: Path,
        request: HttpRequest | None = None,
        url_kwargs: Mapping[str, object] | None = None,
        *,
        dep_cache: dict[str, Any] | None = None,
        chain: BoundaryChain | None = None,
        requested_zones: frozenset[str] | None = None,
    ) -> dict[str, object] | Caught:
        """Build the render context inside the page boundaries of the page.

        A failure one of them contains comes back as `Caught`, holding its shell.
        """
        dep_cache = render_dep_cache(request) if dep_cache is None else dep_cache
        kwargs = url_kwargs or {}
        chain = page_boundaries(file_path) if chain is None else chain
        if not chain.errors:
            return self._built_context(
                file_path, request, kwargs, dep_cache, requested_zones
            )
        if self.awaits(file_path):
            return run_bridged(
                partial(
                    self._scoped_guarded,
                    chain,
                    file_path,
                    request,
                    kwargs,
                    dep_cache,
                    requested_zones,
                )
            )
        with blaming() as blamed:
            outcome = self._contained(
                chain,
                file_path,
                blamed,
                partial(
                    self._context_manager.collect_context,
                    file_path,
                    request,
                    kwargs,
                    dep_cache=dep_cache,
                    requested_zones=requested_zones,
                ),
            )
        tail, _thunk, _entry = self._tail(file_path, request, kwargs, dep_cache, None)
        shell = self._shell(chain, file_path, kwargs, tail, blamed)
        if isinstance(outcome, Caught):
            return replace(outcome, shell=shell)
        return shell.around(outcome)

    def gating_plan(self, file_path: Path, chain: BoundaryChain) -> Plan:
        """Return the split of the callables of `file_path` across its page boundaries.

        It is computed once per graph, registry epoch and set of `loading.djx` files.
        """
        graph = self._context_manager.graph(file_path)
        epoch = self.epoch
        deferred = chain.deferred
        held = self._plans.get(file_path)
        if (
            held is not None
            and held[0] is graph
            and held[1] == epoch
            and held[2] == deferred
        ):
            return held[3]
        resolver = current_resolver()
        entry = contained_chain(self._metadata_registry, file_path)
        metadata = frozenset[str]().union(
            *(
                callable_keys(source.func, resolver)
                for source in entry.tail
                if source.func is not None
            )
        )
        plan = plan_gating(
            graph,
            self._context_manager.inherited_levels(file_path),
            deferred,
            metadata,
            file_path,
        )
        self._plans[file_path] = (graph, epoch, deferred, plan)
        return plan

    def gated_context(
        self,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        *,
        dep_cache: dict[str, Any],
        chain: BoundaryChain,
    ) -> dict[str, object] | Caught:
        """Build the shell context of a page with a `loading.djx`, its guards first.

        The gated callables wait in the `Gate` for the jobs of the page boundaries.
        """
        plan = self.gating_plan(file_path, chain)
        if plan.awaits(plan.split.shell) or self._metadata_registry.awaits(file_path):
            return run_bridged(
                partial(
                    self._scoped_gated, file_path, request, url_kwargs, dep_cache, chain
                )
            )
        started = time.perf_counter()
        phase = Phase(plan.split.shell, {}, {} if settings.DEBUG else None)
        work = partial(
            run_phase,
            plan.graph,
            file_path,
            phase,
            request=request,
            url_kwargs=url_kwargs,
            dep_cache=dep_cache,
        )
        with blaming() as blamed:
            outcome = (
                self._contained(chain, file_path, blamed, work)
                if chain.errors
                else work()
            )
        tail, _thunk, _entry = self._tail(
            file_path, request, url_kwargs, dep_cache, None
        )
        shell = self._shell(chain, file_path, url_kwargs, tail, blamed)
        if isinstance(outcome, Caught):
            return replace(outcome, shell=shell)
        note_slow(plan, file_path, phase.timings, time.perf_counter() - started)
        return self._gate(
            plan, shell, outcome, (file_path, request, url_kwargs, dep_cache)
        )

    async def _scoped_gated(
        self,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        dep_cache: dict[str, Any],
        chain: BoundaryChain,
    ) -> dict[str, object] | Caught:
        """Build the gated shell of an async page inside its own request scope."""
        async with render_scope(dep_cache):
            return await self.agated_context(
                file_path, request, url_kwargs, dep_cache=dep_cache, chain=chain
            )

    async def agated_context(
        self,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        *,
        dep_cache: dict[str, Any],
        chain: BoundaryChain,
    ) -> dict[str, object] | Caught:
        """Build what `gated_context` builds, awaiting the async shell callables."""
        plan = self.gating_plan(file_path, chain)
        started = time.perf_counter()
        phase = Phase(plan.split.shell, {}, {} if settings.DEBUG else None)
        work = arun_phase(
            plan.graph,
            file_path,
            phase,
            request=request,
            url_kwargs=url_kwargs,
            dep_cache=dep_cache,
        )
        with blaming() as blamed:
            outcome = (
                await self._acontained(chain, file_path, blamed, work)
                if chain.errors
                else await work
            )
        caught = isinstance(outcome, Caught)
        registry = (
            self._metadata_registry
            if not caught and self._metadata_registry.awaits(file_path)
            else None
        )
        finish = partial(
            self._tail, file_path, request, url_kwargs, dep_cache, registry
        )
        if registry is None and (request is None or not _get_context_processors()):
            tail, thunk, entry = finish()
        else:
            tail, thunk, entry = await run_segment(finish)
        shell = self._shell(chain, file_path, url_kwargs, tail, blamed)
        if isinstance(outcome, Caught):
            return replace(outcome, shell=shell)
        note_slow(plan, file_path, phase.timings, time.perf_counter() - started)
        context = self._gate(
            plan, shell, outcome, (file_path, request, url_kwargs, dep_cache)
        )
        if entry is not None and entry.folded is None:
            folded = await thunk.afold(entry, dict(context))
            context[METADATA_KEY] = FoldedMetadata(thunk, folded)
        return context

    def _gate(
        self,
        plan: Plan,
        shell: Shell,
        results: dict[int, object],
        render: tuple[Path, HttpRequest | None, Mapping[str, object], dict[str, Any]],
    ) -> dict[str, object]:
        """Return the shell context over `results`, holding the gate of the jobs."""
        file_path, request, url_kwargs, dep_cache = render
        context = shell.around(merged(plan.graph, file_path, results))
        gate = Gate(
            plan, shell.chain, file_path, request, url_kwargs, dep_cache, results
        )
        context[GATE_KEY] = gate
        return context

    async def _scoped_guarded(
        self,
        chain: BoundaryChain,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        dep_cache: dict[str, Any],
        requested_zones: frozenset[str] | None,
    ) -> dict[str, object] | Caught:
        """Build the guarded context of an async page inside its own request scope."""
        async with render_scope(dep_cache):
            return await self.aguarded_context(
                file_path,
                request,
                url_kwargs,
                dep_cache=dep_cache,
                chain=chain,
                requested_zones=requested_zones,
            )

    async def aguarded_context(
        self,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        *,
        dep_cache: dict[str, Any],
        chain: BoundaryChain,
        requested_zones: frozenset[str] | None = None,
    ) -> dict[str, object] | Caught:
        """Build what `guarded_context` builds, awaiting the async page callables.

        The processors and the metadata run once the boundaries settled, outside them.
        """
        with blaming() as blamed:
            outcome = await self._acontained(
                chain,
                file_path,
                blamed,
                self._context_manager.acollect_context(
                    file_path,
                    request,
                    url_kwargs,
                    dep_cache=dep_cache,
                    requested_zones=requested_zones,
                ),
            )
        caught = isinstance(outcome, Caught)
        registry = (
            self._metadata_registry
            if not caught and self._metadata_registry.awaits(file_path)
            else None
        )
        finish = partial(
            self._tail, file_path, request, url_kwargs, dep_cache, registry
        )
        if registry is None and (request is None or not _get_context_processors()):
            tail, thunk, entry = finish()
        else:
            tail, thunk, entry = await run_segment(finish)
        shell = self._shell(chain, file_path, url_kwargs, tail, blamed)
        if isinstance(outcome, Caught):
            return replace(outcome, shell=shell)
        context = shell.around(outcome)
        if entry is not None and entry.folded is None:
            folded = await thunk.afold(entry, dict(context))
            context[METADATA_KEY] = FoldedMetadata(thunk, folded)
        return context

    def _tail(
        self,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        dep_cache: dict[str, Any],
        registry: PageMetadataRegistry | None,
    ) -> tuple[dict[str, object], MetadataThunk, ChainEntry | None]:
        """Return what follows the callables in a context, the processors run into it.

        The metadata chain comes back too when `registry` folds one async.
        """
        thunk = MetadataThunk(
            self._metadata_registry, file_path, request, url_kwargs, dep_cache
        )
        tail: dict[str, object] = {METADATA_KEY: thunk, DEP_CACHE_KEY: dep_cache}
        if request is not None:
            tail[REQUEST_KEY] = request
        return tail, thunk, _finish_context(tail, request, registry, file_path)

    def _shell(
        self,
        chain: BoundaryChain,
        file_path: Path,
        url_kwargs: Mapping[str, object],
        tail: dict[str, object],
        blamed: Blame,
    ) -> Shell:
        """Return the shell of a page around `tail`, cut by what `blamed` kept."""
        info = page_path_info(file_path)
        head: dict[str, object] = {
            TEMPLATE_PATH_KEY: info.template_path,
            PAGE_MODULE_PATH_KEY: info.module_path,
        }
        head.update(url_kwargs)
        levels = self._context_manager.inherited_levels(file_path)
        return Shell(head, tail, levels, blamed, chain)

    def _cut_shell(
        self,
        chain: BoundaryChain,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        dep_cache: dict[str, Any],
        level: int,
    ) -> Shell:
        """Return the shell of a page whose body failed before any callable ran.

        The inherited groups at or above `level` run for it, as their layouts render.
        """
        levels = self._context_manager.inherited_levels(file_path)
        groups = sum(1 for found in levels if found >= level)
        with blaming() as blamed:
            if groups and self._context_manager.awaits(file_path):
                run_bridged(
                    partial(
                        self._scoped_cut,
                        file_path,
                        request,
                        url_kwargs,
                        dep_cache,
                        groups,
                    )
                )
            elif groups:
                self._context_manager.collect_context(
                    file_path, request, url_kwargs, dep_cache=dep_cache, groups=groups
                )
        tail, _thunk, _entry = self._tail(
            file_path, request, url_kwargs, dep_cache, None
        )
        return self._shell(chain, file_path, url_kwargs, tail, blamed)

    async def _scoped_cut(
        self,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        dep_cache: dict[str, Any],
        groups: int,
    ) -> None:
        """Run the leading `groups` inherited groups of an async page for its shell."""
        async with render_scope(dep_cache):
            await self._context_manager.acollect_context(
                file_path, request, url_kwargs, dep_cache=dep_cache, groups=groups
            )

    def _failed_level(self, file_path: Path, blamed: Blame) -> int:
        """Return how many directories up the failed callable sits, `-1` for none."""
        return blamed.level(self._context_manager.inherited_levels(file_path))

    def _contained[T](
        self,
        chain: BoundaryChain,
        file_path: Path,
        blamed: Blame,
        work: Callable[[], T],
    ) -> T | Caught:
        """Return what `work` returns, or the boundary of `chain` holding its failure.

        The protection undoes what the failed part wrote, as the template boundaries do.
        """
        protection = protect()
        try:
            result = work()
        except Exception as exc:
            protection.undo()
            guard = catching(chain, exc, self._failed_level(file_path, blamed))
            if guard is None:
                raise
            return Caught(guard, exc)
        except BaseException:
            protection.undo()
            raise
        protection.keep()
        return result

    async def _acontained[T](
        self, chain: BoundaryChain, file_path: Path, blamed: Blame, work: Awaitable[T]
    ) -> T | Caught:
        """Await `work` under the protection `_contained` gives, on the request thread.

        Every blocking segment of the work runs there, so the savepoint covers it.
        """
        protection = await run_on_request_thread(protect)
        failed = True
        try:
            result = await work
            failed = False
        except Exception as exc:
            guard = catching(chain, exc, self._failed_level(file_path, blamed))
            if guard is None:
                raise
            return Caught(guard, exc)
        finally:
            await run_on_request_thread(protection.undo if failed else protection.keep)
        return result

    async def acontain[T](
        self, chain: BoundaryChain, file_path: Path, work: Awaitable[T]
    ) -> T | Caught:
        """Await `work`, or return the boundary of `chain` that contains its failure."""
        with blaming() as blamed:
            return await self._acontained(chain, file_path, blamed, work)

    def resolve_guarded(
        self,
        file_path: Path,
        module: types.ModuleType | None,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        *,
        dep_cache: dict[str, Any],
        chain: BoundaryChain | None = None,
    ) -> _BodyResolution | Caught:
        """Resolve the body as `resolve_page_body` does, inside the page boundaries."""
        chain = page_boundaries(file_path) if chain is None else chain
        if not chain.errors:
            return self.resolve_page_body(
                file_path, module, request, url_kwargs, dep_cache=dep_cache
            )
        with blaming() as blamed:
            return self._contained(
                chain,
                file_path,
                blamed,
                partial(
                    self.resolve_page_body,
                    file_path,
                    module,
                    request,
                    url_kwargs,
                    dep_cache=dep_cache,
                ),
            )

    def caught_context(
        self,
        file_path: Path,
        caught: Caught,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        *,
        dep_cache: dict[str, Any] | None,
        chain: BoundaryChain,
    ) -> dict[str, object]:
        """Return the shell the boundary that caught renders in, holding the failure.

        A body that failed before any callable ran gets the groups above the cut run.
        """
        level = caught.boundary.level
        shell = caught.shell
        if shell is None:
            cache = render_dep_cache(request) if dep_cache is None else dep_cache
            shell = self._cut_shell(chain, file_path, request, url_kwargs, cache, level)
        context = shell.at(level)
        context[FAILURE_KEY] = caught.error
        return context

    def render_caught(
        self,
        file_path: Path,
        caught: Caught,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        *,
        dep_cache: dict[str, Any],
        started: float | None = None,
        chain: BoundaryChain | None = None,
    ) -> str:
        """Render the error UI of the boundary that caught, inside the layouts above it.

        The layouts and boundaries below it drop out, as the failure skipped them.
        """
        start = time.perf_counter() if started is None else started
        chain = page_boundaries(file_path) if chain is None else chain
        composed = self._layout_loader.compose_failure(file_path, caught.boundary)
        template = Template(
            composed, origin=Origin(str(file_path)), name=str(file_path)
        )
        context = self.caught_context(
            file_path, caught, request, url_kwargs, dep_cache=dep_cache, chain=chain
        )
        with boundary_scope(chain):
            return self._render_template_str(
                file_path,
                template,
                start,
                request,
                url_kwargs,
                dep_cache=dep_cache,
                basis=context,
            )

    def render_composed(
        self,
        file_path: Path,
        body: str,
        request: HttpRequest | None = None,
        url_kwargs: Mapping[str, object] | None = None,
        *,
        dep_cache: dict[str, Any],
        built: Built | None = None,
        skeleton: tuple[str, BoundaryChain] | None = None,
    ) -> str:
        """Compose `body` through layouts and render, over a context `built` ahead.

        Its start is when the build of that context began, so the timing includes it.
        """
        start = time.perf_counter() if built is None else built.started
        text, chain = self.layout_skeleton(file_path) if skeleton is None else skeleton
        composed = self._layout_loader.fill_skeleton(text, body)
        with boundary_scope(chain):
            return self._render_template_str(
                file_path,
                composed,
                start,
                request,
                url_kwargs or {},
                dep_cache=dep_cache,
                basis=chain if built is None else built.context,
            )

    def layout_skeleton(self, file_path: Path) -> tuple[str, BoundaryChain]:
        """Return the cached layout chain of `file_path` with an empty body slot.

        The boundary chain the walk found comes with it, valid exactly while it is.
        """
        templates = self._templates
        skeleton = templates.skeleton.get(file_path)
        if skeleton is None or templates.skeleton_is_stale(file_path):
            skeleton, chain = self._layout_loader.compose_slotted(file_path)
            templates.skeleton[file_path] = skeleton
            templates.skeleton_chains[file_path] = chain
            templates.record_skeleton(file_path)
            # A dynamic page registers no template, so its path facts are dropped here.
            forget_page_path_info(file_path)
            return skeleton, chain
        held = templates.skeleton_chains.get(file_path)
        return skeleton, page_boundaries(file_path) if held is None else held

    def composed_chain(self, file_path: Path) -> BoundaryChain:
        """Return the boundary chain the composed template of `file_path` holds."""
        held = self._templates.composed_chains.get(file_path)
        return page_boundaries(file_path) if held is None else held

    def composed_template_for(self, file_path: Path) -> Template:
        """Return the compiled composed template for the static body.

        The source is held in a local, because the bound may evict it between two reads.
        """
        templates = self._templates
        composed = templates.composed.get(file_path)
        if composed is None or templates.composed_is_stale(file_path):
            templates.forget_composed(file_path)
            module = load_python_module_memo(file_path)
            body = self._load_static_body(file_path, module)
            composed, chain = self._layout_loader.compose_chained(body, file_path)
            self.register_template(file_path, composed)
            templates.composed_chains[file_path] = chain
            templates.record_composed(file_path)
        compiled = templates.compiled.get(file_path)
        if compiled is None:
            # The origin makes compile errors name the page path.
            compiled = Template(
                composed, origin=Origin(str(file_path)), name=str(file_path)
            )
            templates.compiled[file_path] = compiled
        return compiled

    def render_plain(
        self,
        file_path: Path,
        template: Template | str,
        request: HttpRequest,
        url_kwargs: Mapping[str, object],
        *,
        dep_cache: dict[str, Any],
        body: str = "",
    ) -> str:
        """Render a page whose plan found no boundary, deferral or async callable.

        `template` is the compiled page, or the layout skeleton a `render()` body fills.
        Nothing in it can queue a deferred call, so the template renders straight.
        """
        start = time.perf_counter()
        if isinstance(template, str):
            template = Template(self._layout_loader.fill_skeleton(template, body))
        context_data = self._render_context(
            file_path, request, url_kwargs, None, dep_cache
        )
        collector = seed_collector(file_path, context_data)
        html = template.render(DjangoTemplateContext(context_data))
        result = static_assets_slot.get().inject(
            html, collector, page_path=file_path, request=request
        )
        if page_rendered.has_listeners(Page):
            self._announce(file_path, start, request, collector, context_data)
        return result

    def render(
        self,
        file_path: Path,
        request: HttpRequest | None = None,
        url_kwargs: Mapping[str, object] | None = None,
        *,
        dep_cache: dict[str, Any] | None = None,
        template: Template | None = None,
    ) -> str:
        """Render the page with Django `Template` and the static collector.

        The body comes from `template` or a loader, so `render()` is never invoked here.
        """
        start = time.perf_counter()
        if template is None:
            template = self.composed_template_for(file_path)
        chain = self.composed_chain(file_path)
        with boundary_scope(chain):
            return self._render_template_str(
                file_path,
                template,
                start,
                request,
                url_kwargs or {},
                dep_cache=dep_cache,
                basis=chain,
            )

    def authorization_outcome(
        self,
        file_path: Path,
        request: HttpRequest,
        visit_url: str | None,
        url_kwargs: Mapping[str, object] | None = None,
    ) -> tuple[HttpResponseBase | None, bool]:
        """Resolve a page body once, returning its short-circuit response and its kind.

        `render()` sees a GET visit of `visit_url`, a page without it authorizes anyone.
        """
        module, error = load_page_module(file_path)
        if error is not None:
            # Not Http404, since it names the caller URL, and continuing skips guards.
            raise error
        render_func = getattr(module, "render", None) if module is not None else None
        if not callable(render_func):
            return None, False
        visit = visit_request(request, visit_url)
        if is_coroutine_callable(render_func):
            resolution = run_bridged(
                partial(
                    self._authorize, render_func, file_path, visit, url_kwargs or {}
                )
            )
        else:
            resolution = self._call_render_function(
                render_func, file_path, visit, url_kwargs or {}, {}
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

    def create_url_pattern(
        self,
        url_path: str,
        file_path: Path,
        url_parser: URLPatternParser,
        page_view: Callable[[Page, Path], Callable[..., HttpResponseBase] | None],
    ) -> URLPattern | None:
        """Return a `path()` pattern mounting the view `page_view` builds for a page."""
        return create_url_pattern(self, url_path, file_path, url_parser, page_view)


page: Page = Page()
context = page.context


def reset_context_registry() -> None:
    """Clear the shared page-context registry for a from-disk rebuild.

    Paired with the module memo reset so a re-executed `page.py` repopulates it.
    """
    page.contexts.reset()


def reset_metadata_registry() -> None:
    """Clear the shared page-metadata registry, its chain memo and the settings tier."""
    page.reset_metadata()
    forget_metadata_scope()


def reset_page_registrations() -> None:
    """Forget every executed `page.py` and what it registered, to rebuild from disk."""
    reset_module_memo()
    reset_context_registry()
    reset_metadata_registry()
    atomic_declarations.reset()
    page_defer_declarations.reset()
    compress_declarations.reset()
    router_declarations.reset()


__all__ = [
    "Built",
    "Page",
    "context",
    "defers",
    "page",
    "reset_context_registry",
    "reset_metadata_registry",
    "reset_page_registrations",
]
