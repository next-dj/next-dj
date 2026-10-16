"""The public exceptions of the permissions area."""


class PermissionAlreadyRegisteredError(Exception):
    """A permission name is already bound to a different predicate."""

    def __init__(self, name: str) -> None:
        """Name the permission registered twice."""
        super().__init__(
            f"The permission {name!r} is already registered with another predicate"
        )
        self.name = name


__all__ = ["PermissionAlreadyRegisteredError"]
