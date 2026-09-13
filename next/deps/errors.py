"""Exceptions the dependency resolver raises for a graph it cannot serve."""

from collections.abc import Callable, Iterable
from difflib import get_close_matches
from typing import Any, override

from next.utils import describe_callable


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

        The callable being filled is attached afterwards by the resolve that
        failed, so a successful replay owns no callable and pays nothing.
        """
        self.name = name
        self.param_name = param_name
        self.func: Callable[..., Any] | None = None
        matches = get_close_matches(name, registered, n=1)
        self.suggestion: str | None = str(matches[0]) if matches else None
        # Both arguments reach `args`, so the exception survives the copy a
        # process pool or a caching layer makes of it.
        super().__init__(name, param_name)

    def attribute_to(self, func: Callable[..., Any]) -> None:
        """Name `func` as the owner unless an inner resolve already named one."""
        if self.func is None:
            self.func = func

    @override
    def __str__(self) -> str:
        """Compose the message from the name, the parameter, and the owner.

        A registered name close to the missing one replaces the generic advice,
        because a near miss is almost always a typo at the `Depends` site.
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


__all__ = ["DependencyCycleError", "UnknownDependencyError"]
