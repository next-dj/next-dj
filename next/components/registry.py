"""Component registry and visibility resolver."""

from __future__ import annotations

import threading
import weakref
from dataclasses import replace
from typing import TYPE_CHECKING, override

from next.caches import BoundedCache, LruCache

from .deferral import defer_declarations
from .signals import component_registered, components_registered


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence
    from pathlib import Path

    from .info import ComponentInfo


class ErrorFiles:
    """Whether any live component registry holds a component with an `error.djx`.

    A render reads `held` alone, so a project without component boundaries pays nothing.
    """

    __slots__ = ("_counts", "_lock", "held")

    def __init__(self) -> None:
        """Start with no registry holding one."""
        self._counts: dict[int, int] = {}
        self._lock = threading.Lock()
        self.held = False

    def note(self, registry: ComponentRegistry, count: int) -> None:
        """Record how many components of `registry` hold an `error.djx` now."""
        self._set(id(registry), count)

    def forget(self, key: int) -> None:
        """Drop the count of a registry that is gone."""
        self._set(key, 0)

    def _set(self, key: int, count: int) -> None:
        with self._lock:
            if count:
                self._counts[key] = count
            else:
                self._counts.pop(key, None)
            self.held = self._holds()

    def _holds(self) -> bool:
        """Whether a registry counts one, read under the lock."""
        return bool(self._counts)


error_files = ErrorFiles()


class DeferredComponents(ErrorFiles):
    """Whether any component can defer, by its `loading.djx`, module or call site.

    A declaration made after a scan, or a call-site `defer=`, raises it for good.
    """

    __slots__ = ("declared",)

    def __init__(self) -> None:
        """Start with no component able to defer."""
        super().__init__()
        self.declared = False

    def declare(self) -> None:
        """Record a deferred call no registry count holds, once and for good."""
        with self._lock:
            self.declared = True
            self.held = True

    @override
    def _holds(self) -> bool:
        return self.declared or super()._holds()


deferred_components = DeferredComponents()


def may_defer() -> None:
    """Raise for good the flag a page reads, as a component declares or may declare."""
    if not deferred_components.held:
        deferred_components.declare()


defer_declarations.on_deferring(may_defer)


def _forget(key: int) -> None:
    """Drop both counts of a registry that is gone."""
    error_files.forget(key)
    deferred_components.forget(key)


def _tally(infos: Sequence[ComponentInfo]) -> tuple[int, int]:
    """Count the `infos` holding an `error.djx` and those able to defer.

    One pass reads the files, and the declared modes cost nothing while none exists.
    """
    filed = [files for info in infos if (files := info.files) is not None]
    guarded = sum(1 for files in filed if files.error is not None)
    deferring = sum(1 for files in filed if files.loading is not None)
    modules = defer_declarations.deferring_modules()
    if modules:
        deferring += sum(
            1
            for info in infos
            if info.module_path in modules
            and (info.files is None or info.files.loading is None)
        )
    return guarded, deferring


class ComponentRegistry:
    """Holds discovered components and whether a directory is a global root."""

    def __init__(self) -> None:
        """Create empty internal lists, indices, and the initial version."""
        self._ordered: list[ComponentInfo] = []
        self._by_name: dict[str, list[ComponentInfo]] = {}
        self._root_roots: set[Path] = set()
        self._version = 0
        self._lock = threading.Lock()
        self._error_files = 0
        self._deferring = 0
        weakref.finalize(self, _forget, id(self))

    @property
    def version(self) -> int:
        """Monotonic counter bumped on every mutation."""
        return self._version

    def _bump(self) -> None:
        self._version += 1

    def register(self, component: ComponentInfo) -> None:
        """Append one component and index it by name."""
        with self._lock:
            self._ordered.append(component)
            self._by_name.setdefault(component.name, []).append(component)
            self._count_added((component,))
            self._bump()
        component_registered.send(sender=ComponentRegistry, info=component)

    def register_many(self, components: Iterable[ComponentInfo]) -> None:
        """Index every component from the iterable in order.

        Like `bulk_create`, fires only the plural `components_registered` signal.
        """
        added = tuple(components)
        if not added:
            return
        with self._lock:
            self._ordered.extend(added)
            by_name = self._by_name
            for c in added:
                by_name.setdefault(c.name, []).append(c)
            self._count_added(added)
            self._bump()
        components_registered.send(sender=ComponentRegistry, infos=added)

    def refresh_files(self) -> bool:
        """Swap in the boundary files on disk now, answering whether any entry changed.

        Concurrent calls agree, as a swap applies only to the entry it was read from.
        """
        swaps: dict[int, tuple[ComponentInfo, ComponentInfo]] = {}
        for info in tuple(self._ordered):
            current = info.current_files()
            if current != info.files:
                swaps[id(info)] = (info, replace(info, files=current))
        if not swaps:
            return False
        with self._lock:
            ordered = [self._swapped(info, swaps) for info in self._ordered]
            if all(new is old for new, old in zip(ordered, self._ordered, strict=True)):
                return False
            by_name: dict[str, list[ComponentInfo]] = {}
            for info in ordered:
                by_name.setdefault(info.name, []).append(info)
            self._ordered = ordered
            self._by_name = by_name
            guarded, deferring = _tally(ordered)
            self._count_error_files(guarded)
            self._count_deferring(deferring)
            self._bump()
        return True

    def _count_added(self, added: Sequence[ComponentInfo]) -> None:
        """Raise both counts by what `added` holds, held under the lock."""
        guarded, deferring = _tally(added)
        if guarded:
            self._count_error_files(self._error_files + guarded)
        if deferring:
            self._count_deferring(self._deferring + deferring)

    def _count_error_files(self, count: int) -> None:
        """Keep the count of components holding an `error.djx`, held under the lock."""
        self._error_files = count
        error_files.note(self, count)

    def _count_deferring(self, count: int) -> None:
        """Keep the count of components able to defer, held under the lock."""
        self._deferring = count
        deferred_components.note(self, count)

    @staticmethod
    def _swapped(
        info: ComponentInfo, swaps: dict[int, tuple[ComponentInfo, ComponentInfo]]
    ) -> ComponentInfo:
        """Return the entry that replaces `info`, or `info` itself when none does."""
        swap = swaps.get(id(info))
        return swap[1] if swap is not None and swap[0] is info else info

    def get_all(self) -> Sequence[ComponentInfo]:
        """Return an immutable view of every registered component."""
        return tuple(self._ordered)

    def mark_as_root(self, path: Path) -> None:
        """Mark `path` as globally visible across the tree, either spelling.

        The resolved form goes in too, so a symlinked root still answers `is_root`.
        """
        self._root_roots.add(path)
        self._root_roots.add(path.resolve())

    def is_root(self, path: Path) -> bool:
        """Return True when `path` was marked as a global root."""
        return path in self._root_roots

    def global_roots(self) -> frozenset[Path]:
        """Return every path marked as a global root, as an immutable set."""
        return frozenset(self._root_roots)

    def clear(self) -> None:
        """Drop every registered component and reset tracked roots."""
        with self._lock:
            self._ordered.clear()
            self._by_name.clear()
            self._root_roots.clear()
            self._count_error_files(0)
            self._count_deferring(0)
            self._bump()

    def __len__(self) -> int:
        """Return the number of registered components."""
        return len(self._ordered)

    def __contains__(self, name: str) -> bool:
        """Return True when a component with `name` has been registered."""
        return name in self._by_name

    def __iter__(self) -> Iterator[ComponentInfo]:
        """Iterate over components in registration order."""
        return iter(self._ordered)


class ComponentVisibilityResolver:
    """Decides which component names exist for a given template file path."""

    def __init__(self, registry: ComponentRegistry) -> None:
        """Bind the resolver to a `ComponentRegistry` and allocate caches."""
        self._registry = registry
        self._path_cache: LruCache[tuple[Path, Path], list[str] | None] = LruCache()
        self._result_cache: LruCache[Path, Mapping[str, ComponentInfo]] = LruCache()
        self._scope_index: dict[
            Path, tuple[int, list[tuple[int, int, ComponentInfo]]]
        ] = {}
        self._scope_index_registry_version = -1
        self._cached_registry_version = -1
        self._resolved_path_cache: BoundedCache[Path, Path] = BoundedCache()

    def _ensure_scope_index(self) -> None:
        if self._scope_index_registry_version == self._registry.version:
            return
        self._scope_index = {}
        for position, ci in enumerate(self._registry.get_all()):
            root = ci.resolved_scope_root
            group = self._scope_index.get(root)
            if group is None:
                dirs_origin = 1 if self._registry.is_root(root) else 0
                group = (dirs_origin, [])
                self._scope_index[root] = group
            group[1].append((position, group[0], ci))
        self._scope_index_registry_version = self._registry.version

    def _candidate_components(
        self, template_path: Path
    ) -> list[tuple[int, int, ComponentInfo]]:
        self._ensure_scope_index()
        tmpl_dir = template_path.parent
        out: list[tuple[int, int, ComponentInfo]] = []
        for scope_root, (dirs_origin, infos) in self._scope_index.items():
            if dirs_origin:
                out.extend(infos)
                continue
            try:
                tmpl_dir.relative_to(scope_root)
            except ValueError:
                continue
            else:
                out.extend(infos)
        return out

    def resolve_visible(self, template_path: Path) -> Mapping[str, ComponentInfo]:
        """Return a mapping of visible component names for `template_path`.

        The path memo keys on the spelling passed, so repeated renders keep hitting it.
        """
        if self._cached_registry_version != self._registry.version:
            self._result_cache.clear()
            self._path_cache.clear()
            self._resolved_path_cache.clear()
            self._scope_index_registry_version = -1
            self._cached_registry_version = self._registry.version

        resolved = self._resolved_path_cache.get(template_path)
        if resolved is None:
            resolved = template_path.resolve()
            self._resolved_path_cache[template_path] = resolved

        try:
            return self._result_cache[resolved]
        except KeyError:
            pass

        candidates: list[tuple[int, int, str, int, ComponentInfo]] = []
        for position, dirs_origin, component in self._candidate_components(resolved):
            score = self._calculate_visibility_score(component, resolved)
            if score is not None:
                candidates.append(
                    (score, dirs_origin, component.name, position, component)
                )

        # Higher score wins, then page tree over shared DIRS, then discovery order.
        candidates.sort(key=lambda x: (-x[0], x[1], x[2], x[3]))

        seen: set[str] = set()
        result: dict[str, ComponentInfo] = {}
        for _score, _origin, name, _position, info in candidates:
            if name not in seen:
                result[name] = info
                seen.add(name)

        self._result_cache[resolved] = result
        return result

    def _calculate_visibility_score(
        self, component: ComponentInfo, template_path: Path
    ) -> int | None:
        scope_root = component.resolved_scope_root
        scope_rel = component.scope_relative or ""

        if self._registry.is_root(scope_root) and not scope_rel:
            return 0

        parts = self._get_relative_parts_cached(template_path, scope_root)
        if parts is None or scope_rel not in parts:
            return None

        return len(scope_rel) if scope_rel else 0

    def _get_relative_parts_cached(
        self, template_path: Path, scope_root: Path
    ) -> list[str] | None:
        cache_key = (template_path, scope_root)
        try:
            return self._path_cache[cache_key]
        except KeyError:
            pass
        value = self._compute_relative_parts(template_path, scope_root)
        self._path_cache[cache_key] = value
        return value

    def _compute_relative_parts(
        self, template_path: Path, scope_root: Path
    ) -> list[str] | None:
        try:
            template_dir = template_path.parent
            rel = template_dir.relative_to(scope_root)
            parts = rel.parts
            if not parts:
                return [""]
            return ["/".join(parts[:i]) if i else "" for i in range(len(parts), -1, -1)]
        except ValueError:
            return None


__all__ = [
    "ComponentRegistry",
    "ComponentVisibilityResolver",
    "DeferredComponents",
    "ErrorFiles",
    "deferred_components",
    "error_files",
    "may_defer",
]
