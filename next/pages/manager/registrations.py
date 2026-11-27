"""What a `page.py` registers on the page manager, its context and its metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, overload

from next.deps.cache import render_dep_cache
from next.introspect import declared_file, registering_file
from next.pages.metadata.chain import (
    ChainEntry,
    MetadataDeclaration,
    StaticMetadata,
    chain_entry,
    declared_metadata,
    fold_chain,
    static_metadata,
)


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, MutableMapping
    from pathlib import Path

    from django.http import HttpRequest

    from next.pages.metadata import Metadata
    from next.pages.metadata.markers import Segment
    from next.pages.metadata.registry import MetadataRegistrations, PageMetadataRegistry
    from next.pages.registry import PageContextRegistry, ZoneBinding
    from next.static.serializers import JsContextSerializer


class PageRegistrations:
    """Register the `@context` and the metadata callables a `page.py` declares."""

    _context_manager: PageContextRegistry
    _metadata_registry: PageMetadataRegistry

    @property
    def contexts(self) -> PageContextRegistry:
        """Return the registry of every `@context`, read by the checks and the tools."""
        return self._context_manager

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
        guard: bool = False,
    ) -> Callable[[C], C]: ...
    def context(
        self,
        func_or_key: Callable[..., Any] | str | None = None,
        *,
        inherit_context: bool = False,
        serialize: bool = False,
        serializer: JsContextSerializer | None = None,
        zone: str | None = None,
        guard: bool = False,
    ) -> Callable[..., Any]:
        """Register a keyed or dict-merge `@context` for the file declaring `func`.

        `serialize=True` publishes, `zone=` binds, `guard=True` runs before a shell.
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
            if guard:
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


__all__ = ["PageRegistrations"]
