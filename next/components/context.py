"""Context registration for `component.py` modules.

The file declaring each callable is recorded, so only its component's context runs.
"""

from __future__ import annotations

import sys
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, overload

from next.deps import RESERVED_KEYS
from next.utils import (
    MisattributedContext,
    MisattributionLog,
    callable_name,
    defining_file,
    resolved_tree,
    store_capped,
)


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from next.static.serializers import JsContextSerializer


# Bounded because a render may spell a component path no earlier render spelled,
# and a project holds far fewer components than the bound ever reaches.
_LOOKUP_CACHE_MAX_SIZE = 2048


@dataclass(frozen=True, slots=True)
class ContextFunction:
    """One function registered to add variables before a component template runs.

    The optional `serializer` overrides the global JS context serializer for the value
    this callable produces, but only when `serialize` is true.
    """

    func: Callable[..., Any]
    key: str | None
    serialize: bool = False
    serializer: JsContextSerializer | None = None


class ComponentContextRegistry:
    """Maps `component.py` paths to functions that supply template variables."""

    def __init__(self) -> None:
        """Create an empty path-keyed context-function mapping."""
        self._registry: dict[Path, dict[str | None, ContextFunction]] = {}
        self._misattributions = MisattributionLog()
        self._version = 0
        self._lookup_cache: OrderedDict[Path, tuple[ContextFunction, ...]] = (
            OrderedDict()
        )
        self._lookup_version = 0

    @property
    def version(self) -> int:
        """Monotonic counter bumped on every write to the registry."""
        return self._version

    def _bump(self) -> None:
        """Invalidate the lookup memo by advancing the registry version."""
        self._version += 1

    def misattributed(self) -> tuple[MisattributedContext, ...]:
        """Return every registration bound to a file other than the one running it."""
        return self._misattributions.entries()

    def note_misattribution(
        self, registered_from: Path, declared_in: Path, func: Callable[..., Any]
    ) -> None:
        """Record a `@component.context` declared outside the running file.

        The registration binds to `declared_in`, which no render of
        `registered_from` reads, so the pair feeds the `next.E075` diagnostic.
        """
        self._misattributions.record(registered_from, declared_in, func)

    def registered_names(self) -> dict[Path, tuple[str, ...]]:
        """Return the callable names registered per file, for the diagnostics."""
        return {
            file_path: tuple(callable_name(entry.func) for entry in entries.values())
            for file_path, entries in self._registry.items()
        }

    def register(
        self,
        component_path: Path,
        key: str | None,
        func: Callable[..., Any],
        *,
        serialize: bool = False,
        serializer: JsContextSerializer | None = None,
    ) -> None:
        """Register `func` under `key` for `component_path`, rejecting reserved keys."""
        path = resolved_tree(component_path)

        if isinstance(key, str) and key in RESERVED_KEYS:
            msg = (
                f"Component context key {key!r} is reserved for dependency injection. "
                f"Use another name. Reserved: {sorted(RESERVED_KEYS)}."
            )
            raise ValueError(msg)

        component_registry = self._registry.setdefault(path, {})

        if key in component_registry:
            existing = component_registry[key]
            if not self._is_same_function(existing.func, func):
                if key is None:
                    dup_desc = "unkeyed @component.context"
                else:
                    dup_desc = f"key {key!r}"
                msg = (
                    f"Duplicate component context registration ({dup_desc}) for {path}"
                )
                raise ValueError(msg)

        entry = ContextFunction(
            func=func, key=key, serialize=serialize, serializer=serializer
        )
        if component_registry.get(key) == entry:
            # Re-registering an identical entry leaves the memo valid.
            return

        component_registry[key] = entry
        self._bump()

    def unregister(self, component_path: Path) -> None:
        """Drop every context function registered for `component_path`."""
        if self._registry.pop(resolved_tree(component_path), None) is not None:
            self._bump()

    def get_functions(self, component_path: Path) -> Sequence[ContextFunction]:
        """Return a tuple of registered context functions for `component_path`.

        Results are memoised under the path as passed and thrown away when the registry
        version moves, so a warm render pays neither the resolve nor the tuple build. A
        symlinked spelling costs its own entry and answers what its plain one answers,
        because the registry behind both is keyed by the resolved path. The empty result
        is memoised too, because most components register no context function at all.
        """
        if self._lookup_version != self._version:
            self._lookup_cache.clear()
            self._lookup_version = self._version
        cached = self._lookup_cache.get(component_path)
        if cached is not None:
            return cached
        resolved = resolved_tree(component_path)
        functions = tuple(self._registry.get(resolved, {}).values())
        store_capped(
            self._lookup_cache, component_path, functions, _LOOKUP_CACHE_MAX_SIZE
        )
        return functions

    def _is_same_function(
        self, func1: Callable[..., Any], func2: Callable[..., Any]
    ) -> bool:
        if func1 is func2:
            return True
        if callable_name(func1) != callable_name(func2):
            return False
        try:
            return defining_file(func1).resolve() == defining_file(func2).resolve()
        except (OSError, TypeError):
            return False

    def __len__(self) -> int:
        """Return the total number of registered context functions."""
        return sum(len(funcs) for funcs in self._registry.values())


class ComponentContextManager:
    """Registers and looks up context helpers used from `component.py`."""

    def __init__(self) -> None:
        """Create an empty registry for context callables."""
        self._registry = ComponentContextRegistry()

    @overload
    def context[C: Callable[..., Any]](self, func_or_key: C, /) -> C: ...
    @overload
    def context[C: Callable[..., Any]](
        self,
        func_or_key: str | None = None,
        *,
        serialize: bool = False,
        serializer: JsContextSerializer | None = None,
    ) -> Callable[[C], C]: ...
    def context(
        self,
        func_or_key: Callable[..., Any] | str | None = None,
        *,
        serialize: bool = False,
        serializer: JsContextSerializer | None = None,
    ) -> Callable[..., Any]:
        """Mark a function so it fills template variables for this component module.

        `serialize=True` publishes the return value on `window.Next.context`, and
        `serializer=` overrides the global `JS_CONTEXT_SERIALIZER` for this key.
        """
        # Captured here rather than inside the decorator so both spellings see
        # the component.py that ran `@component.context`, not this module.
        registered_from = Path(sys._getframe(1).f_code.co_filename)

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            declared_in = defining_file(func)
            if declared_in != registered_from:
                self._registry.note_misattribution(registered_from, declared_in, func)
            key = None if callable(func_or_key) else func_or_key
            self._registry.register(
                declared_in, key, func, serialize=serialize, serializer=serializer
            )
            return func

        return decorator(func_or_key) if callable(func_or_key) else decorator

    def get_functions(self, component_path: Path) -> Sequence[ContextFunction]:
        """Return context callables registered for this `component.py` path."""
        return self._registry.get_functions(component_path)


component = ComponentContextManager()
context = component.context


__all__ = [
    "ComponentContextManager",
    "ComponentContextRegistry",
    "ContextFunction",
    "component",
    "context",
]
