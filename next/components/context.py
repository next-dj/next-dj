"""Context registration for `component.py` modules.

`ComponentContextManager` is the public handle used by decorator
`@component.context` inside a `component.py` file. It records the
caller's path so the right context callables run when the matching
component template is rendered.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from next.deps import resolver
from next.utils import caller_source_path


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


@dataclass(frozen=True, slots=True)
class ContextFunction:
    """One function registered to add variables before a component template runs."""

    func: Callable[..., Any]
    key: str | None
    serialize: bool = False


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
            func=func, key=key, serialize=serialize
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
        name1 = getattr(func1, "__name__", None)
        name2 = getattr(func2, "__name__", None)
        if not name1 or not name2 or name1 != name2:
            return False
        try:
            file1 = inspect.getsourcefile(func1)
            file2 = inspect.getsourcefile(func2)
            if not file1 or not file2:
                return False
            return Path(file1).resolve() == Path(file2).resolve()
        except (OSError, TypeError, ValueError):
            return False

    def __len__(self) -> int:
        """Return the total number of registered context functions."""
        return sum(len(funcs) for funcs in self._registry.values())


class ComponentContextManager:
    """Registers and looks up context helpers used from `component.py`."""

    def __init__(self) -> None:
        """Create an empty registry for context callables."""
        self._registry = ComponentContextRegistry()

    def _get_caller_path(self, back_count: int = 1) -> Path:
        return caller_source_path(
            back_count=back_count,
            max_walk=10,
            skip_framework_file=("context.py", "components"),
        )

    def context(
        self,
        func_or_key: Callable[..., Any] | str | None = None,
        *,
        serialize: bool = False,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Mark a function so it fills template variables for this component module.

        Pass `serialize=True` to include the return value in
        `Next.context` so JavaScript code on the page can read it via
        `window.Next.context`.
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if callable(func_or_key):
                caller_path = self._get_caller_path(2)
                self._registry.register(
                    caller_path, None, func_or_key, serialize=serialize
                )
            else:
                caller_path = self._get_caller_path(1)
                self._registry.register(
                    caller_path, func_or_key, func, serialize=serialize
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
