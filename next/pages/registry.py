"""Per-`page.py` context-callable registry and layout watch helpers.

`PageContextRegistry` stores the list of context functions bound to
each `page.py` path, and merges their return values (with keyed and
dict-merge semantics) at render time. The watch helpers list
`template.djx` and `layout.djx` files under page roots for the
autoreloader and for the static finder.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, NamedTuple

from next.deps import DependencyResolver, get_request_dep_cache, resolver
from next.utils import MisattributedContext, MisattributionLog, callable_name

from .context import ContextResult
from .signals import context_registered
from .watch import get_pages_directories_for_watch


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from django.http import HttpRequest

    from next.static.serializers import JsContextSerializer


class PageContextEntry(NamedTuple):
    """One context callable registered for a `page.py` file.

    The optional `serializer` overrides the global JS context
    serializer for the value this callable produces, but only when
    `serialize` is true. The optional `zones` binds the callable to the
    named zones, so a GET for a foreign zone never calls it. Backed by
    `NamedTuple` so the hot `register_context` path allocates a plain
    tuple rather than a frozen dataclass instance.
    """

    func: Callable[..., Any]
    inherit_context: bool
    serialize: bool
    serializer: JsContextSerializer | None = None
    zones: frozenset[str] | None = None


class ZoneBinding(NamedTuple):
    """One registered `@context` seen through its zone binding.

    The zone diagnostics pair a zone-bound callable with the callables
    reading its key, so they need the zones next to the callable itself
    while the rest of the entry stays inside the registry. A `zones` of
    `None` marks a callable every render runs.
    """

    key: str | None
    name: str
    zones: frozenset[str] | None
    func: Callable[..., Any]


logger = logging.getLogger(__name__)


_MAX_ANCESTOR_WALK_DEPTH = 64


def get_layout_djx_paths_for_watch() -> set[Path]:
    """Return every `layout.djx` path under page trees."""
    result: set[Path] = set()
    for pages_path in get_pages_directories_for_watch():
        try:
            for path in pages_path.rglob("layout.djx"):
                result.add(path.resolve())
        except OSError as e:
            logger.debug("Cannot rglob layout.djx under %s: %s", pages_path, e)
    return result


def get_template_djx_paths_for_watch() -> set[Path]:
    """Return every `template.djx` path under page trees."""
    result: set[Path] = set()
    for pages_path in get_pages_directories_for_watch():
        try:
            for path in pages_path.rglob("template.djx"):
                result.add(path.resolve())
        except OSError as e:
            logger.debug("Cannot rglob template.djx under %s: %s", pages_path, e)
    return result


class PageContextRegistry:
    """Register per-`page.py` context callables and merge their output."""

    def __init__(self, resolver: DependencyResolver | None = None) -> None:
        """Initialise with an optional resolver and an empty registry."""
        self._context_registry: dict[Path, dict[str | None, PageContextEntry]] = {}
        # Keyless callables share the `None` slot, so the registry keeps only
        # the last. Retain the overwritten names for the `next.E018` diagnostic.
        self._keyless_conflicts: dict[Path, list[str]] = {}
        self._misattributions = MisattributionLog()
        self._resolver = resolver

    def _get_resolver(self) -> DependencyResolver:
        """Return the injected resolver or the shared singleton."""
        if self._resolver is not None:
            return self._resolver
        return resolver

    def reset(self) -> None:
        """Drop every registered context so the next import repopulates it.

        Re-executing a `page.py` only overwrites the keys it still declares, so
        a removed `@context` would otherwise leave a stale entry behind.
        """
        self._context_registry.clear()
        self._keyless_conflicts.clear()
        self._misattributions.clear()

    def misattributed(self) -> tuple[MisattributedContext, ...]:
        """Return every registration bound to a file other than the one running it."""
        return self._misattributions.entries()

    def note_misattribution(
        self, registered_from: Path, declared_in: Path, func: Callable[..., Any]
    ) -> None:
        """Record a `@context` whose callable was declared outside the running file.

        The registration binds to `declared_in`, which no render of
        `registered_from` reads, so the pair feeds the `next.E074` diagnostic.
        """
        self._misattributions.record(registered_from, declared_in, func)

    def registered_names(self) -> dict[Path, tuple[str, ...]]:
        """Return the callable names registered per file, for the diagnostics."""
        return {
            file_path: tuple(callable_name(entry.func) for entry in entries.values())
            for file_path, entries in self._context_registry.items()
        }

    def zone_bindings(self) -> dict[Path, tuple[ZoneBinding, ...]]:
        """Return the zone view of the callables registered per file, for the checks.

        The zone diagnostics read a callable's zones, its key, and its
        signature, and this keeps them off the registry storage itself.
        """
        return {
            file_path: tuple(
                ZoneBinding(
                    key=key,
                    name=callable_name(entry.func),
                    zones=entry.zones,
                    func=entry.func,
                )
                for key, entry in entries.items()
            )
            for file_path, entries in self._context_registry.items()
        }

    def register_context(
        self,
        file_path: Path,
        key: str | None,
        func: Callable[..., Any],
        *,
        inherit_context: bool = False,
        serialize: bool = False,
        serializer: JsContextSerializer | None = None,
        zone: str | None = None,
    ) -> None:
        """Bind `func` to `file_path` with keyed or dict-merge semantics.

        A `zone` name scopes the callable to that zone, so a GET for any
        other zone skips it entirely.
        """
        if zone is not None and inherit_context:
            msg = (
                "`@context` cannot combine `zone=` with `inherit_context=True`, "
                "an ancestor page.py cannot reference a descendant template's zone."
            )
            raise ValueError(msg)
        bucket = self._context_registry.setdefault(file_path, {})
        existing = bucket.get(None)
        # Compare by name so a re-executed module (same name) is not a conflict.
        if key is None and existing is not None:
            existing_name = callable_name(existing.func)
            new_name = callable_name(func)
            if existing_name != new_name:
                self._keyless_conflicts.setdefault(file_path, [existing_name]).append(
                    new_name
                )
        bucket[key] = PageContextEntry(
            func=func,
            inherit_context=inherit_context,
            serialize=serialize,
            serializer=serializer,
            zones=None if zone is None else frozenset({zone}),
        )
        context_registered.send(
            sender=PageContextRegistry, file_path=file_path, key=key
        )

    def collect_context(
        self,
        file_path: Path,
        request: HttpRequest | None = None,
        *,
        requested_zones: frozenset[str] | None = None,
        **kwargs,
    ) -> ContextResult:
        """Merge inherited ancestor page.py context with this file's context callables.

        Inherited context comes from ``@context(..., inherit_context=True)``
        callables in ancestor ``page.py`` files, not from layout files.
        The returned `ContextResult` separates the full template context
        from the JavaScript-serializable subset. The js_context uses
        first-registration semantics so that page-level values always
        take priority over inherited ones. A `requested_zones` batch narrows
        this file's callables to the zone-less ones plus those bound to a
        named zone in the batch, a full render passes no batch and runs
        every callable.
        """
        context_data: dict[str, Any] = {}
        js_context: dict[str, Any] = {}
        js_context_serializers: dict[str, JsContextSerializer] = {}
        # Reuse the dispatch dep_cache on a validation-failure re-render, so a
        # Depends("name") the form action resolved is not recomputed here.
        shared = get_request_dep_cache(request)
        dep_cache: dict[str, Any] = shared if shared is not None else {}
        dep_stack: list[str] = []

        inherited_context = self._collect_inherited_context(
            file_path, request, kwargs, dep_cache, dep_stack
        )
        context_data.update(inherited_context)

        registry = self._context_registry.get(file_path, {})
        ordered = sorted(
            registry.items(), key=lambda item: (item[0] is not None, str(item[0] or ""))
        )
        for key, entry in ordered:
            # `isdisjoint` tests the zone batch without allocating an
            # intersection.
            if (
                requested_zones is not None
                and entry.zones is not None
                and entry.zones.isdisjoint(requested_zones)
            ):
                continue
            resolved = self._get_resolver().resolve_dependencies(
                entry.func,
                request=request,
                _cache=dep_cache,
                _stack=dep_stack,
                _context_data=context_data,
                **kwargs,
            )
            result = entry.func(**resolved)
            if key is None:
                context_data.update(result)
                if entry.serialize:
                    for k, v in result.items():
                        if k not in js_context:
                            js_context[k] = v
                            if entry.serializer is not None:
                                js_context_serializers[k] = entry.serializer
            else:
                context_data[key] = result
                if entry.serialize and key not in js_context:
                    js_context[key] = result
                    if entry.serializer is not None:
                        js_context_serializers[key] = entry.serializer

        return ContextResult(
            context_data=context_data,
            js_context=js_context,
            js_context_serializers=js_context_serializers,
        )

    def _collect_inherited_context(
        self,
        file_path: Path,
        request: HttpRequest | None,
        url_kwargs: dict[str, object],
        dep_cache: dict[str, Any],
        dep_stack: list[str],
    ) -> dict[str, Any]:
        """Return values from ancestor `page.py` callables marked `inherit_context`.

        Walks ancestor directories that contain a `page.py` and runs every
        `@context(..., inherit_context=True)` callable registered there.
        A sibling `layout.djx` is not required.
        The shared HTML envelope can live one level up under
        ``PAGE_BACKENDS["DIRS"]``, and pages declaring inheritable context
        should still surface it on descendant routes.
        """
        inherited_context = {}
        current_dir = file_path.parent

        for _ in range(_MAX_ANCESTOR_WALK_DEPTH):
            if current_dir == current_dir.parent:
                break

            page_file = current_dir / "page.py"

            if page_file.exists():
                for key, entry in self._context_registry.get(page_file, {}).items():
                    if entry.inherit_context:
                        resolved = self._get_resolver().resolve_dependencies(
                            entry.func,
                            request=request,
                            _cache=dep_cache,
                            _stack=dep_stack,
                            **url_kwargs,
                        )
                        if key is None:
                            inherited_context.update(entry.func(**resolved))
                        else:
                            inherited_context[key] = entry.func(**resolved)

            current_dir = current_dir.parent

        return inherited_context
