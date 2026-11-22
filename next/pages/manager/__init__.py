"""The `Page` manager and its process-wide singleton.

`context` is an alias of `page.context`, which user code applies as `@context`.
"""

from __future__ import annotations

import time
from functools import partial
from typing import TYPE_CHECKING, Any

from django.template import Context as DjangoTemplateContext, Origin, Template

from next.caches import PageCache
from next.components.registry import deferred_components
from next.deps.cache import render_dep_cache
from next.pages.access import PageDeclarations
from next.pages.boundaries import Caught, boundary_scope
from next.pages.compression import CompressDeclaring, compress_store
from next.pages.defer import PageDeferDeclaring, page_defer_store
from next.pages.gating import GATE_KEY, Plan
from next.pages.loaders import (
    BoundaryChain,
    LayoutTemplateLoader,
    build_registered_loaders,
    forget_layout_texts,
    load_python_module_memo,
    page_boundaries,
    reset_module_memo,
)
from next.pages.metadata.registry import PageMetadataRegistry
from next.pages.metadata.scope import forget_metadata_scope
from next.pages.paths import clear_page_path_info, forget_page_path_info
from next.pages.registry import PageContextRegistry
from next.pages.responses import depth_of, purpose_of
from next.pages.router import RouterDeclaring, router_store
from next.pages.signals import page_rendered, template_loaded
from next.pages.transactions import AtomicDeclaring, atomic_store
from next.ports import render_deferred, static_assets_slot
from next.seeding import seed_collector

from .bodies import PageBodies
from .gating import PageGating
from .patterns import create_url_pattern
from .registrations import PageRegistrations
from .render import Built
from .templates import PageTemplateCache


if TYPE_CHECKING:
    import types
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from django.http import HttpRequest
    from django.http.response import HttpResponseBase
    from django.urls import URLPattern

    from next.static import StaticCollector
    from next.urls import URLPatternParser


def defers(chain: BoundaryChain | None = None) -> bool:
    """Return whether a render may queue a deferred call, so it opens a gathering block.

    A page boundary with a `loading.djx` in `chain` or any deferrable component does.
    """
    return deferred_components.held or (chain is not None and bool(chain.deferred))


class Page(
    PageRegistrations,
    PageGating,
    PageBodies,
    PageDeclarations,
    AtomicDeclaring,
    PageDeferDeclaring,
    CompressDeclaring,
    RouterDeclaring,
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
        self._colours: PageCache[Path, tuple[int, bool, bool]] = PageCache()
        self._plans: PageCache[Path, tuple[object, int, object, Plan]] = PageCache()
        self._context_manager = PageContextRegistry(on_change=self._recolour)
        self._metadata_registry = PageMetadataRegistry(on_change=self._recolour)

    def _recolour(self) -> None:
        """Move the epoch the memoised colours carry, after a write to a registry."""
        self.epoch += 1

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
    atomic_store.reset()
    page_defer_store.reset()
    compress_store.reset()
    router_store.reset()


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
