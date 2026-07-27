"""Context registration for `component.py` modules.

`ComponentContextManager` is the public handle used by decorator
`@component.context` inside a `component.py` file. It records the
caller's path so the right context callables run when the matching
component template is rendered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, overload

from next.checks.common import get_components_manager
from next.deps import resolver
from next.utils import callable_name, defining_file

from .backends import FileComponentsBackend


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from pathlib import Path

    from next.static.serializers import JsContextSerializer


@dataclass(frozen=True, slots=True)
class ContextFunction:
    """One function registered to add variables before a component template runs.

    The optional `serializer` overrides the global JS context
    serializer for the value this callable produces, but only when
    `serialize` is true.
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
        path = component_path.resolve()

        if isinstance(key, str) and key in resolver.EXPLICIT_RESOLVE_KEYS:
            msg = (
                f"Component context key {key!r} is reserved for dependency injection. "
                f"Use another name. Reserved: {sorted(resolver.EXPLICIT_RESOLVE_KEYS)}."
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

        component_registry[key] = ContextFunction(
            func=func, key=key, serialize=serialize, serializer=serializer
        )

    def get_functions(self, component_path: Path) -> Sequence[ContextFunction]:
        """Return a tuple of registered context functions for `component_path`."""
        path = component_path.resolve()
        registry = self._registry.get(path, {})
        return tuple(registry.values())

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

        Pass `serialize=True` to include the return value in
        `Next.context` so JavaScript code on the page can read it via
        `window.Next.context`. Pass `serializer=` to route this key
        through a custom `JsContextSerializer` instead of the global
        `JS_CONTEXT_SERIALIZER` setting.
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if callable(func_or_key):
                self._registry.register(
                    defining_file(func),
                    None,
                    func_or_key,
                    serialize=serialize,
                    serializer=serializer,
                )
            else:
                self._registry.register(
                    defining_file(func),
                    func_or_key,
                    func,
                    serialize=serialize,
                    serializer=serializer,
                )
            return func

        return decorator(func_or_key) if callable(func_or_key) else decorator

    def get_functions(self, component_path: Path) -> Sequence[ContextFunction]:
        """Return context callables registered for this `component.py` path."""
        return self._registry.get_functions(component_path)


component = ComponentContextManager()
context = component.context


def iter_serialized_component_context_keys() -> Iterator[tuple[Path, str]]:
    """Yield the `component.py` path and key of every keyed `serialize=True` context.

    A keyless `serialize=True` callable spreads the keys of the dict it
    returns at render time, so those keys exist only at runtime and never
    travel through here. Reading the keys imports every `component.py`, since
    the decorator state is the truth, so a check calling this pays that import
    even under `LAZY_COMPONENT_MODULES`.
    """
    manager = get_components_manager()
    for backend in manager._backends:
        if not isinstance(backend, FileComponentsBackend):
            continue
        for module_path in backend.loaded_module_paths():
            for entry in component.get_functions(module_path):
                if entry.serialize and entry.key is not None:
                    yield module_path, entry.key


__all__ = [
    "ComponentContextManager",
    "ComponentContextRegistry",
    "ContextFunction",
    "component",
    "context",
    "iter_serialized_component_context_keys",
]
