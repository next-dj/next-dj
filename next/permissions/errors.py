"""The public exceptions of the permissions area."""

from pathlib import Path


class PermissionAlreadyRegisteredError(Exception):
    """A permission name is already bound to a different predicate."""

    def __init__(self, name: str) -> None:
        """Name the permission registered twice."""
        super().__init__(
            f"The permission {name!r} is already registered with another predicate"
        )
        self.name = name


class DeclarationOutsideModuleError(RuntimeError):
    """An access declaration runs while no loader executes the file it binds to."""

    def __init__(self, source: Path) -> None:
        """Name the file the declaration bound to."""
        super().__init__(
            f"An access declaration binds to {source}, which no page or component "
            "loader is executing. Declare access at the top level of the page.py or "
            "component.py it guards, never in a helper or at request time."
        )
        self.source = source


class UnknownAccessObjectError(LookupError):
    """An `obj=` of an access declaration names no context its endpoint produces."""

    def __init__(self, name: str, endpoint: Path) -> None:
        """Name the missing context and the endpoint whose chain lacks it."""
        super().__init__(
            f"The access of {endpoint} checks a permission on the context {name!r}, "
            "which no context callable of its chain produces."
        )
        self.name = name
        self.endpoint = endpoint


__all__ = [
    "DeclarationOutsideModuleError",
    "PermissionAlreadyRegisteredError",
    "UnknownAccessObjectError",
]
