"""How a page builds its render context, plain or inside its page boundaries."""

from __future__ import annotations

import logging
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Any, NamedTuple

from next.conf import next_framework_settings
from next.deps.cache import render_dep_cache
from next.deps.runner import run_segment
from next.deps.scope import bridged_scope
from next.pages.boundaries import FAILURE_KEY, Caught, ErrorContext
from next.pages.graph import blaming
from next.pages.loaders import BoundaryChain, page_boundaries
from next.pages.metadata.chain import (
    ChainEntry,
    FoldedMetadata,
    MetadataThunk,
    contained_chain,
)
from next.pages.paths import page_path_info
from next.pages.processors import _get_context_processors
from next.seeding import (
    DEP_CACHE_KEY,
    JS_CONTEXT_KEY,
    JS_SERIALIZERS_KEY,
    METADATA_KEY,
    PAGE_MODULE_PATH_KEY,
    REQUEST_KEY,
    TEMPLATE_PATH_KEY,
)

from .containment import PageContainment


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from django.http import HttpRequest

    from next.caches import PageCache
    from next.pages.graph import Blame
    from next.pages.metadata.registry import PageMetadataRegistry


logger = logging.getLogger("next.pages.manager")


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


class Built(NamedTuple):
    """A render context built ahead of its template render and when the build began."""

    context: dict[str, object]
    started: float


class ContextRender(PageContainment):
    """Build the render context of a page, the shell its error UI renders in too."""

    epoch: int
    _colours: PageCache[Path, tuple[int, bool, bool]]
    _metadata_registry: PageMetadataRegistry

    def awaits(self, file_path: Path) -> bool:
        """Return whether a render of `file_path` runs async context or metadata.

        Memoised against the epoch both registries move, the read a sync render pays.
        """
        held = self._colours.get(file_path)
        if held is None or held[0] != self.epoch:
            held = self._colour(file_path)
        return held[1]

    def _metadata_awaits(self, file_path: Path) -> bool:
        """Return whether the metadata chain of `file_path` runs an async callable."""
        held = self._colours.get(file_path)
        if held is None or held[0] != self.epoch:
            held = self._colour(file_path)
        return held[2]

    def _colour(self, file_path: Path) -> tuple[int, bool, bool]:
        """Memoise whether a render of `file_path` awaits, its metadata alone too.

        The epoch is read first, so a write during the read leaves the memo stale.
        """
        epoch = self.epoch
        metadata = self._metadata_registry.awaits(file_path)
        found = metadata or self._context_manager.awaits(file_path)
        held = self._colours[file_path] = (epoch, found, metadata)
        return held

    def build_render_context(
        self,
        file_path: Path,
        request: HttpRequest | None = None,
        url_kwargs: Mapping[str, object] | None = None,
        *,
        dep_cache: dict[str, Any] | None = None,
        requested_zones: frozenset[str] | None = None,
    ) -> dict[str, object]:
        """Build the full render context dict used by `render`.

        An async page runs through the bridge the server binds, in a scope of its own.
        """
        cache = render_dep_cache(request) if dep_cache is None else dep_cache
        kwargs = url_kwargs or {}
        if self.awaits(file_path):
            return bridged_scope(
                cache,
                partial(
                    self.arender_context,
                    file_path,
                    request,
                    kwargs,
                    dep_cache=cache,
                    requested_zones=requested_zones,
                ),
            )
        return self._render_context(file_path, request, kwargs, requested_zones, cache)

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
        registry = self._metadata_registry if self._metadata_awaits(file_path) else None
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
            return self.build_render_context(
                file_path,
                request,
                kwargs,
                dep_cache=dep_cache,
                requested_zones=requested_zones,
            )
        if self.awaits(file_path):
            return bridged_scope(
                dep_cache,
                partial(
                    self.aguarded_context,
                    file_path,
                    request,
                    kwargs,
                    dep_cache=dep_cache,
                    chain=chain,
                    requested_zones=requested_zones,
                ),
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
        shell, thunk, entry = await self._ashell(
            chain,
            (file_path, request, url_kwargs, dep_cache),
            blamed,
            caught=isinstance(outcome, Caught),
        )
        if isinstance(outcome, Caught):
            return replace(outcome, shell=shell)
        context = shell.around(outcome)
        if entry is not None and entry.folded is None:
            folded = await thunk.afold(entry, dict(context))
            context[METADATA_KEY] = FoldedMetadata(thunk, folded)
        return context

    async def _ashell(
        self,
        chain: BoundaryChain,
        render: tuple[Path, HttpRequest | None, Mapping[str, object], dict[str, Any]],
        blamed: Blame,
        *,
        caught: bool,
    ) -> tuple[ErrorContext, MetadataThunk, ChainEntry | None]:
        """Return the shell of a settled async body, its processors run off the loop.

        The metadata chain comes back too when a body that was not caught folds one.
        """
        file_path, request, url_kwargs, dep_cache = render
        registry = (
            self._metadata_registry
            if not caught and self._metadata_awaits(file_path)
            else None
        )
        finish = partial(
            self._tail, file_path, request, url_kwargs, dep_cache, registry
        )
        if registry is None and (request is None or not _get_context_processors()):
            tail, thunk, entry = finish()
        else:
            tail, thunk, entry = await run_segment(finish)
        return self._shell(chain, file_path, url_kwargs, tail, blamed), thunk, entry

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
    ) -> ErrorContext:
        """Return the shell of a page around `tail`, cut by what `blamed` kept."""
        info = page_path_info(file_path)
        head: dict[str, object] = {
            TEMPLATE_PATH_KEY: info.template_path,
            PAGE_MODULE_PATH_KEY: info.module_path,
        }
        head.update(url_kwargs)
        levels = self._context_manager.inherited_levels(file_path)
        return ErrorContext(head, tail, levels, blamed, chain)

    def _cut_shell(
        self,
        chain: BoundaryChain,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: Mapping[str, object],
        dep_cache: dict[str, Any],
        level: int,
    ) -> ErrorContext:
        """Return the shell of a page whose body failed before any callable ran.

        The inherited groups at or above `level` run for it, as their layouts render.
        """
        levels = self._context_manager.inherited_levels(file_path)
        groups = sum(1 for found in levels if found >= level)
        with blaming() as blamed:
            if groups and self._context_manager.awaits(file_path):
                bridged_scope(
                    dep_cache,
                    partial(
                        self._context_manager.acollect_context,
                        file_path,
                        request,
                        url_kwargs,
                        dep_cache=dep_cache,
                        groups=groups,
                    ),
                )
            elif groups:
                self._context_manager.collect_context(
                    file_path, request, url_kwargs, dep_cache=dep_cache, groups=groups
                )
        tail, _thunk, _entry = self._tail(
            file_path, request, url_kwargs, dep_cache, None
        )
        return self._shell(chain, file_path, url_kwargs, tail, blamed)

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


__all__ = ["Built", "ContextRender"]
