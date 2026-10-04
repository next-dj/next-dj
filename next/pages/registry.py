"""Per-`page.py` context-callable registry, keyed by the file that declared each one."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, NamedTuple

from next.caches import PageCache
from next.deps.cache import render_dep_cache
from next.deps.resolver import current_resolver
from next.introspect import MisattributedContext, MisattributionLog, callable_name

from .context import ContextResult
from .errors import PageContextShapeError
from .paths import page_path_info
from .signals import context_registered


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from django.http import HttpRequest

    from next.static.serializers import JsContextSerializer


class PageContextEntry(NamedTuple):
    """One context callable registered for a `page.py` file.

    `zones` binds the callable to the named zones, so a GET for another zone skips it.
    A `NamedTuple` keeps each registration as cheap as a plain tuple.
    """

    func: Callable[..., Any]
    inherit_context: bool
    serialize: bool
    serializer: JsContextSerializer | None = None
    zones: frozenset[str] | None = None


class ZoneBinding(NamedTuple):
    """One registered `@context` with its zone binding, as the checks read it.

    `zones=None` means every render runs the callable.
    """

    key: str | None
    name: str
    zones: frozenset[str] | None
    func: Callable[..., Any]


logger = logging.getLogger(__name__)


type _OrderedEntries = tuple[tuple[str | None, PageContextEntry], ...]


def _merge_rank(item: tuple[str | None, PageContextEntry]) -> tuple[bool, str]:
    """Rank one callable of a file, keyless first and keyed by the key string."""
    return item[0] is not None, item[0] or ""


def _in_merge_order(entries: dict[str | None, PageContextEntry]) -> _OrderedEntries:
    """Order the callables of one file the way its merge consumes them."""
    return tuple(sorted(entries.items(), key=_merge_rank))


class _PageOrder(NamedTuple):
    """The callables of the page itself in run order, with the keys the merge keeps.

    `shielded` names the keyed values set before the keyless callable runs, which its
    dict merge does not overwrite.
    """

    entries: _OrderedEntries
    shielded: frozenset[str]


def _page_order(entries: dict[str | None, PageContextEntry]) -> _PageOrder:
    """Order the page callables, the inheritable ones first in declaration order.

    Descendants run the inheritable ones in the same order, so they see the same values
    as the page.
    """
    inherited = tuple(item for item in entries.items() if item[1].inherit_context)
    if not inherited:
        return _PageOrder(_in_merge_order(entries), frozenset())
    rest = _in_merge_order(
        {key: entry for key, entry in entries.items() if not entry.inherit_context}
    )
    ordered = inherited + rest
    before: list[str] = []
    for key, _entry in ordered:
        if key is None:
            return _PageOrder(ordered, frozenset(before))
        before.append(key)
    return _PageOrder(ordered, frozenset())


def _keyless_shape_error(
    func: Callable[..., Any], file_path: Path
) -> PageContextShapeError:
    """Return the error a keyless `@context` returning a non-mapping raises."""
    return PageContextShapeError(callable_name(func), file_path)


def _keyless_result(
    result: object, entry: PageContextEntry, file_path: Path, shielded: frozenset[str]
) -> dict[str, Any]:
    """Return the mapping a keyless callable returned, without the shielded keys."""
    if not isinstance(result, dict):
        raise _keyless_shape_error(entry.func, file_path)
    if not shielded:
        return result
    return {name: value for name, value in result.items() if name not in shielded}


class PageContextRegistry:
    """Register per-`page.py` context callables and merge their output."""

    def __init__(self) -> None:
        """Start with an empty registry and no memoised merge order."""
        self._context_registry: dict[Path, dict[str | None, PageContextEntry]] = {}
        # Keyless callables share the `None` slot, so the registry keeps only
        # the last. Retain the overwritten names for the `next.E018` diagnostic.
        self._keyless_conflicts: dict[Path, list[str]] = {}
        self._misattributions = MisattributionLog()
        self._version = 0
        self._memo_version = 0
        # Bounded, because a router may name page paths without end, while the
        # registry holds only the files a `@context` ran in.
        self._merge_order: PageCache[Path, _PageOrder] = PageCache()
        self._inheritable: PageCache[Path, tuple[_OrderedEntries, ...]] = PageCache()

    @property
    def version(self) -> int:
        """Return a monotonic counter incremented on every write to the registry.

        The per-path memos compare it to detect a write since they were built.
        """
        return self._version

    def _bump(self) -> None:
        """Increment the version so the per-path memos rebuild."""
        self._version += 1

    def reset(self) -> None:
        """Drop every registered context so the next import repopulates it.

        Re-executing a `page.py` only overwrites the keys it still declares, so
        a removed `@context` would otherwise leave a stale entry behind.
        """
        self._context_registry.clear()
        self._keyless_conflicts.clear()
        self._misattributions.clear()
        self._bump()

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
        """Return the zone view of the callables registered per file, for the checks."""
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

    def keyless_conflicts(self) -> dict[Path, tuple[str, ...]]:
        """Return the keyless callables that overwrote one another, per file."""
        return {
            file_path: tuple(names)
            for file_path, names in self._keyless_conflicts.items()
        }

    def serialized_keys(self) -> dict[Path, tuple[str, ...]]:
        """Return the keys every keyed `serialize=True` callable publishes, per file.

        A keyless one spreads the keys of its result at render time and declares none.
        """
        return {
            file_path: tuple(
                key
                for key, entry in entries.items()
                if key is not None and entry.serialize
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

        A `zone` name scopes the callable, so a GET for any other zone skips it.
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
        self._bump()
        context_registered.send(
            sender=PageContextRegistry, file_path=file_path, key=key
        )

    def collect_context(
        self,
        file_path: Path,
        request: HttpRequest | None = None,
        *,
        dep_cache: dict[str, Any] | None = None,
        _requested_zones: frozenset[str] | None = None,
        **kwargs,
    ) -> ContextResult:
        """Merge inherited ancestor page.py context with this file's context callables.

        Inherited context comes from `inherit_context=True` callables in ancestor
        `page.py` files, not layouts, and first registration wins for the js_context.
        """
        context_data: dict[str, Any] = {}
        js_context: dict[str, Any] = {}
        js_context_serializers: dict[str, JsContextSerializer] = {}
        dep_cache = render_dep_cache(request) if dep_cache is None else dep_cache
        dep_stack: list[str] = []

        inherited_context = self._collect_inherited_context(
            file_path, request, kwargs, dep_cache, dep_stack
        )
        context_data.update(inherited_context)

        # Read once for the whole merge, because each attribute read on the shared
        # holder is forwarded to the active resolver.
        active = current_resolver()
        order = self._page_order(file_path)
        for key, entry in order.entries:
            # `isdisjoint` tests the batch without allocating an intersection.
            if (
                _requested_zones is not None
                and entry.zones is not None
                and entry.zones.isdisjoint(_requested_zones)
            ):
                continue
            resolved = active.resolve_dependencies(
                entry.func,
                request=request,
                _cache=dep_cache,
                _stack=dep_stack,
                _context_data=context_data,
                **kwargs,
            )
            result = entry.func(**resolved)
            if key is None:
                result = _keyless_result(result, entry, file_path, order.shielded)
                context_data.update(result)
                if entry.serialize:
                    # Only a shielded key can precede the one keyless callable.
                    js_context.update(result)
                    if entry.serializer is not None:
                        for k in result:
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

        Outer files run first and keep their keys, each file in declaration order.
        """
        inherited_context: dict[str, Any] = {}
        groups = self._inheritable_groups(file_path)
        if not groups:
            return inherited_context
        active = current_resolver()
        for group in groups:
            merged: tuple[str, ...] = ()
            for key, entry in group:
                if key is not None and key in inherited_context and key not in merged:
                    continue
                resolved = active.resolve_dependencies(
                    entry.func,
                    request=request,
                    _cache=dep_cache,
                    _stack=dep_stack,
                    _context_data=inherited_context,
                    **url_kwargs,
                )
                result = entry.func(**resolved)
                if key is not None:
                    inherited_context[key] = result
                    continue
                if not isinstance(result, dict):
                    raise _keyless_shape_error(entry.func, file_path)
                merged = tuple(name for name in result if name not in inherited_context)
                for name in merged:
                    inherited_context[name] = result[name]
        return inherited_context

    def _sync_memos(self) -> None:
        """Drop the per-path memos once the registry changed after they were built."""
        if self._memo_version != self._version:
            self._merge_order.clear()
            self._inheritable.clear()
            self._memo_version = self._version

    def _page_order(self, file_path: Path) -> _PageOrder:
        """Return this file's callables in the order its own merge consumes them."""
        self._sync_memos()
        order = self._merge_order.get(file_path)
        if order is None:
            order = _page_order(self._context_registry.get(file_path, {}))
            self._merge_order[file_path] = order
        return order

    def _inheritable_groups(self, file_path: Path) -> tuple[_OrderedEntries, ...]:
        """Return the inheritable callables of every ancestor file, outermost first.

        The page itself is left out, because its own merge runs those callables.
        """
        self._sync_memos()
        groups = self._inheritable.get(file_path)
        if groups is None:
            groups = tuple(self._build_inheritable_groups(file_path))
            self._inheritable[file_path] = groups
        return groups

    def _build_inheritable_groups(self, file_path: Path) -> Iterator[_OrderedEntries]:
        """Yield the inheritable callables of each ancestor file from the registry.

        No directory is probed, so a deleted `page.py` contributes until a reload.
        """
        for ancestor in reversed(page_path_info(file_path).ancestors):
            entries = self._context_registry.get(ancestor)
            if ancestor == file_path or not entries:
                continue
            group = tuple(
                (key, entry) for key, entry in entries.items() if entry.inherit_context
            )
            if group:
                yield group
