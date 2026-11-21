"""Exceptions the dependency resolver raises for a graph it cannot serve."""

from collections.abc import Callable, Iterable
from difflib import get_close_matches
from typing import Any, override

from django.core.exceptions import ImproperlyConfigured

from next.errors import AsyncCallableError
from next.introspect import describe_callable, is_coroutine_callable


class AsyncDependencyError(AsyncCallableError):
    """Raised when a resolver meets an async callable it cannot call for a value.

    A subclass of `AsyncCallableError`, so the module load checks report it alike.
    """

    def __init__(self, func: Callable[..., Any], *, name: str | None = None) -> None:
        """Describe `func`, the named dependency `name` binds when one is given."""
        role = (
            "dependency-injected callable" if name is None else f"dependency {name!r}"
        )
        super().__init__(role, describe_callable(func))
        if is_coroutine_callable(func):
            message = (
                f"The {role} {self.subject} is async, so the sync resolver would hand "
                "back a coroutine nobody awaits. Resolve it on the async path instead, "
                "await resolver.aresolve(func, context) for its arguments or await "
                "resolver.acall(func, context) for its value."
            )
        else:
            message = (
                f"The {role} {self.subject} is an async generator, so either "
                "resolver would hand back a stream nobody iterates. Declare it with "
                "'async def' and return the value, then await resolver.acall(func, "
                "context) for it."
            )
        self.args = (message,)


class DependencyCycleError(Exception):
    """Raised when dependency resolution re-enters a key already in progress."""

    def __init__(self, cycle: list[str]) -> None:
        """Record the offending dependency chain for the error message."""
        self.cycle = cycle
        super().__init__(f"Circular dependency: {' -> '.join(cycle)}")


class MissingKeyError(LookupError):
    """Raised when a loader's batch function returns no value for a key it was given.

    It is no `KeyError`, so a template lookup or an `except KeyError` cannot hide it.
    """

    def __init__(self, key: object) -> None:
        """Keep the key the message names."""
        super().__init__(key)
        self.key = key

    @override
    def __str__(self) -> str:
        """Name the key in a sentence."""
        return f"The batch function returned no value for the key {self.key!r}."


class UnknownDependencyError(LookupError):
    """Raised when `Depends` names a dependency that nothing has registered."""

    def __init__(
        self, name: str, param_name: str | None, *, registered: Iterable[str] = ()
    ) -> None:
        """Record the missing name, the parameter, and the closest registered name.

        The failed resolve attaches the callable later, so a good replay pays nothing.
        """
        self.name = name
        self.param_name = param_name
        self.func: Callable[..., Any] | None = None
        matches = get_close_matches(name, registered, n=1)
        self.suggestion: str | None = str(matches[0]) if matches else None
        # Both arguments reach `args`, so a pickled copy survives.
        super().__init__(name, param_name)

    def attribute_to(self, func: Callable[..., Any]) -> None:
        """Name `func` as the owner unless an inner resolve already named one."""
        if self.func is None:
            self.func = func

    @override
    def __str__(self) -> str:
        """Compose the message from the name, the parameter, and the owner.

        A near-miss registered name replaces the generic advice, as it is likely a typo.
        """
        where = "" if self.param_name is None else f' on parameter "{self.param_name}"'
        owner = "" if self.func is None else f" of {describe_callable(self.func)}"
        advice = (
            f'Register it with resolver.dependency("{self.name}") or fix the name.'
            if self.suggestion is None
            else f'Did you mean "{self.suggestion}"?'
        )
        return (
            f'Depends("{self.name}"){where}{owner} names a dependency nothing '
            f"registered. {advice}"
        )


class UnboundObjectError(ImproperlyConfigured):
    """Raised when a `DObject` parameter finds no URL kwarg or parent to look up by.

    A missing source is a wiring mistake, not a missing row, so it never reads as 404.
    """

    def __init__(
        self,
        param_name: str,
        key: str,
        *,
        parent: bool = False,
        available: Iterable[str] = (),
        declared: bool = False,
    ) -> None:
        """Record the parameter, or the model of an `obj=`, and the key it lacks."""
        self.param_name = param_name
        self.key = key
        self.parent = parent
        captured = ", ".join(sorted(available)) or "none"
        if declared:
            message = (
                f"permission_required(obj=DObject[{param_name}]) finds no URL kwarg "
                f'"{key}" (captured: {captured}). It reads the kwarg named after the '
                f"model, so capture it as a [{key}] segment."
            )
        elif parent:
            message = (
                f'DObject parameter "{param_name}" finds no "{key}" in the context '
                f'it resolves in. Declare @context("{key}", inherit_context=True) in '
                "an ancestor page.py, since a form handler or hook resolves with no "
                "page context."
            )
        else:
            message = (
                f'DObject parameter "{param_name}" finds no URL kwarg "{key}" '
                f"(captured: {captured}). It reads the kwarg named like itself, so "
                "rename it after a captured segment."
            )
        super().__init__(message)


__all__ = [
    "AsyncDependencyError",
    "DependencyCycleError",
    "MissingKeyError",
    "UnboundObjectError",
    "UnknownDependencyError",
]
