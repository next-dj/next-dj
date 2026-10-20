"""Exceptions the dependency resolver raises for a graph it cannot serve."""

from collections.abc import Callable, Iterable
from difflib import get_close_matches
from typing import Any, override

from django.core.exceptions import ImproperlyConfigured

from next.introspect import describe_callable


class DependencyCycleError(Exception):
    """Raised when dependency resolution re-enters a key already in progress."""

    def __init__(self, cycle: list[str]) -> None:
        """Record the offending dependency chain for the error message."""
        self.cycle = cycle
        super().__init__(f"Circular dependency: {' -> '.join(cycle)}")


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


__all__ = ["DependencyCycleError", "UnboundObjectError", "UnknownDependencyError"]
