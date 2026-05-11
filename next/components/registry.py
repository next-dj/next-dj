"""Component registry and visibility resolver.

`ComponentRegistry` is the ordered collection of discovered
`ComponentInfo` entries used by a backend. It tracks which scope
roots were registered as globally visible and exposes a version
counter used by `ComponentVisibilityResolver` to invalidate its
caches.

`ComponentVisibilityResolver` decides which component names are in
scope for a given template file path. It lazily builds a scope index
from the registry and caches per-template results.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

from .signals import component_registered, components_registered


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence
    from pathlib import Path

    from .info import ComponentInfo


_VISIBILITY_CACHE_MAX_SIZE = 2048


class ComponentRegistry:
    """Holds discovered components and whether a directory is a global root."""

    def __init__(self) -> None:
        """Create empty internal lists, indices, and the initial version."""
        self._ordered: list[ComponentInfo] = []
        self._by_name: dict[str, list[ComponentInfo]] = {}
        self._root_roots: set[Path] = set()
        self._version = 0

    @property
    def version(self) -> int:
        """Monotonic counter bumped on every mutation."""
        return self._version

    def _bump(self) -> None:
        self._version += 1

    def register(self, component: ComponentInfo) -> None:
        """Append one component and index it by name."""
        self._ordered.append(component)
        self._by_name.setdefault(component.name, []).append(component)
        self._bump()
        component_registered.send(sender=ComponentRegistry, info=component)

    def register_many(self, components: Iterable[ComponentInfo]) -> None:
        """Index every component from the iterable in order.

        Follows the Django bulk convention (`bulk_create` skips
        per-instance `post_save`). Receivers that need per-item
        events should subscribe to `components_registered` and read
        the `infos` tuple. The singular `component_registered` is
        not fired from this path.
        """
        added = tuple(components)
        if not added:
            return
        for c in added:
            self._ordered.append(c)
            self._by_name.setdefault(c.name, []).append(c)
        self._bump()
        components_registered.send(sender=ComponentRegistry, infos=added)

    def get_all(self) -> Sequence[ComponentInfo]:
        """Return an immutable view of every registered component."""
        return tuple(self._ordered)

    def mark_as_root(self, path: Path) -> None:
        """Mark `path` as globally visible across the tree."""
        self._root_roots.add(path)

    def is_root(self, path: Path) -> bool:
        """Return True when `path` was marked as a global root."""
        return path in self._root_roots

    def clear(self) -> None:
        """Drop every registered component and reset tracked roots."""
        self._ordered.clear()
        self._by_name.clear()
        self._root_roots.clear()
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
        self._path_cache: OrderedDict[tuple[Path, Path], list[str] | None] = (
            OrderedDict()
        )
        self._result_cache: OrderedDict[Path, Mapping[str, ComponentInfo]] = (
            OrderedDict()
        )
        self._scope_index: dict[Path, list[ComponentInfo]] = {}
        self._scope_index_registry_version = -1
        self._cached_registry_version = -1
        self._resolved_path_cache: dict[Path, Path] = {}

    def _ensure_scope_index(self) -> None:
        if self._scope_index_registry_version == self._registry.version:
            return
        self._scope_index = {}
        for ci in self._registry.get_all():
            self._scope_index.setdefault(ci.resolved_scope_root, []).append(ci)
        self._scope_index_registry_version = self._registry.version

    def _candidate_components(self, template_path: Path) -> list[ComponentInfo]:
        self._ensure_scope_index()
        tmpl_dir = template_path.parent
        out: list[ComponentInfo] = []
        for scope_root, infos in self._scope_index.items():
            if self._registry.is_root(scope_root):
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
        """Return a mapping of visible component names for `template_path`."""
        cached_resolved = self._resolved_path_cache.get(template_path)
        if cached_resolved is None:
            cached_resolved = template_path.resolve()
            self._resolved_path_cache[template_path] = cached_resolved
        template_path = cached_resolved

        if self._cached_registry_version != self._registry.version:
            self._result_cache.clear()
            self._path_cache.clear()
            self._resolved_path_cache.clear()
            self._resolved_path_cache[template_path] = template_path
            self._scope_index_registry_version = -1
            self._cached_registry_version = self._registry.version

        if template_path in self._result_cache:
            self._result_cache.move_to_end(template_path)
            return self._result_cache[template_path]

        candidates: list[tuple[int, str, ComponentInfo]] = []
        for component in self._candidate_components(template_path):
            score = self._calculate_visibility_score(component, template_path)
            if score is not None:
                candidates.append((score, component.name, component))

        candidates.sort(key=lambda x: (-x[0], x[1]))

        seen: set[str] = set()
        result: dict[str, ComponentInfo] = {}
        for _score, name, info in candidates:
            if name not in seen:
                result[name] = info
                seen.add(name)

        self._result_cache[template_path] = result
        if len(self._result_cache) > _VISIBILITY_CACHE_MAX_SIZE:
            self._result_cache.popitem(last=False)
        return result

    def _calculate_visibility_score(
        self, component: ComponentInfo, template_path: Path
    ) -> int | None:
        scope_root = component.scope_root
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
        if cache_key in self._path_cache:
            self._path_cache.move_to_end(cache_key)
            return self._path_cache[cache_key]
        value = self._compute_relative_parts(template_path, scope_root)
        self._path_cache[cache_key] = value
        if len(self._path_cache) > _VISIBILITY_CACHE_MAX_SIZE:
            self._path_cache.popitem(last=False)
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

    def clear_cache(self) -> None:
        """Drop every cached visibility result and scope index."""
        self._path_cache.clear()
        self._result_cache.clear()
        self._scope_index_registry_version = -1


__all__ = ["ComponentRegistry", "ComponentVisibilityResolver"]
