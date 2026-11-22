"""Context registration for `component.py` modules.

The file declaring each callable is recorded, so only its component's context runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, overload

from next.caches import BoundedCache
from next.defer import DeferMode, WrapperTag
from next.deps import RESERVED_KEYS, AsyncDependencyError
from next.introspect import (
    MisattributedContext,
    MisattributionLog,
    callable_name,
    declared_file,
    defining_file,
    is_async_callable,
    is_coroutine_callable,
    registering_file,
    registering_site,
)
from next.permissions.access import declaring, require_login, require_permission
from next.utils import resolved_tree

from .defer import defer_declarations


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from next.static.serializers import JsContextSerializer


def awaits(func: Callable[..., Any]) -> bool:
    """Return whether `func` is a coroutine function, refusing an async generator.

    A generator would hand the render a stream nobody iterates, so it never registers.
    """
    if not is_async_callable(func):
        return False
    if not is_coroutine_callable(func):
        raise AsyncDependencyError(func)
    return True


@dataclass(frozen=True, slots=True)
class ContextFunction:
    """One function registered to add variables before a component template runs.

    The optional `serializer` overrides the global one when `serialize` is set.
    """

    func: Callable[..., Any]
    key: str | None
    serialize: bool = False
    serializer: JsContextSerializer | None = None
    is_async: bool = False


type ContextLookup = tuple[tuple[ContextFunction, ...], bool]


class ComponentContextRegistry:
    """Maps `component.py` paths to functions that supply template variables."""

    def __init__(self) -> None:
        """Create an empty path-keyed context-function mapping."""
        self._registry: dict[Path, dict[str | None, ContextFunction]] = {}
        self._misattributions = MisattributionLog()
        self._version = 0
        # Bounded because a render may spell a component path no earlier render did.
        self._lookup_cache: BoundedCache[Path, ContextLookup] = BoundedCache()
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

        The registration binds to `declared_in`, so the pair feeds `next.E075`.
        """
        self._misattributions.record(registered_from, declared_in, func)

    def registered_names(self) -> dict[Path, tuple[str, ...]]:
        """Return the callable names registered per file, for the diagnostics."""
        return {
            file_path: tuple(callable_name(entry.func) for entry in entries.values())
            for file_path, entries in self._registry.items()
        }

    def registered(self) -> dict[Path, tuple[ContextFunction, ...]]:
        """Return the context functions registered per `component.py`, in order."""
        return {
            file_path: tuple(entries.values())
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
        is_async = awaits(func)
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
            func=func,
            key=key,
            serialize=serialize,
            serializer=serializer,
            is_async=is_async,
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
        """Return a tuple of registered context functions for `component_path`."""
        return self.lookup(component_path)[0]

    def lookup(self, component_path: Path) -> ContextLookup:
        """Return the context functions of `component_path` and whether any is async.

        Memoised per path spelling until the registry version moves.
        """
        if self._lookup_version != self._version:
            self._lookup_cache.clear()
            self._lookup_version = self._version
        cached = self._lookup_cache.get(component_path)
        if cached is not None:
            return cached
        resolved = resolved_tree(component_path)
        functions = tuple(self._registry.get(resolved, {}).values())
        found = (functions, any(entry.is_async for entry in functions))
        self._lookup_cache[component_path] = found
        return found

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


def _context_name(obj: object) -> str | None:
    """Return the context name `obj=` gives, refusing anything but a non-empty str."""
    if obj is None or (isinstance(obj, str) and obj):
        return obj
    msg = "component.permission_required(obj=) names a context of the component."
    raise TypeError(msg)


class ComponentContextManager:
    """Registers and looks up context helpers used from `component.py`.

    Its access declarations bind the `component.py` that makes them and nothing else.
    """

    def __init__(self) -> None:
        """Create an empty registry for context callables."""
        self._registry = ComponentContextRegistry()

    @property
    def registry(self) -> ComponentContextRegistry:
        """Return the registry of every `@component.context`, read by the checks."""
        return self._registry

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

        `serialize=True` publishes the value on `window.Next.context` via `serializer`.
        """
        registered_from = registering_file()

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            declared_in = declared_file(
                func, registered_from, self._registry.note_misattribution
            )
            key = None if callable(func_or_key) else func_or_key
            self._registry.register(
                declared_in, key, func, serialize=serialize, serializer=serializer
            )
            return func

        return decorator(func_or_key) if callable(func_or_key) else decorator

    def login_required(self) -> None:
        """Require a logged-in user, the way Django's `login_required` does."""
        require_login(declaring(registering_site()))

    def permission_required(
        self, perm: str | tuple[str, ...], *, obj: str | None = None
    ) -> None:
        """Require every permission in `perm`, checked on the context `obj` names."""
        name = _context_name(obj)
        require_permission(declaring(registering_site()), perm, name)

    def defer(
        self,
        mode: DeferMode | str = DeferMode.STREAM,
        *,
        timeout: float | str | None = None,
        props: tuple[str, ...] = (),
        tag: str | WrapperTag | None = WrapperTag.SITE,
        parallel: bool | None = None,
        tokens: Mapping[str, object] | None = None,
    ) -> None:
        """Deliver this component as a boundary in `mode`, with the options given.

        `next.E222` reports an option outside its shape, which declares nothing.
        """
        defer_declarations.defer(
            registering_site(),
            {
                "mode": mode,
                "timeout": timeout,
                "props": props,
                "tag": tag,
                "parallel": parallel,
                "tokens": tokens,
            },
        )

    def props(self, *names: str) -> None:
        """Declare the props a reload or a retry of this component renders from."""
        defer_declarations.props(registering_site(), names)

    def get_functions(self, component_path: Path) -> Sequence[ContextFunction]:
        """Return context callables registered for this `component.py` path."""
        return self._registry.get_functions(component_path)

    def lookup(self, component_path: Path) -> ContextLookup:
        """Return the context callables of this path and whether any one is async."""
        return self._registry.lookup(component_path)


component = ComponentContextManager()
context = component.context


__all__ = [
    "ComponentContextManager",
    "ComponentContextRegistry",
    "ContextFunction",
    "ContextLookup",
    "awaits",
    "component",
    "context",
]
