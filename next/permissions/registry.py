"""Registry of the object permissions a project checks by predicate, keyed by name."""

from collections.abc import Iterator

from .errors import PermissionAlreadyRegisteredError
from .markers import Predicate, Test, predicate as leaf
from .signals import permission_registered


_NAME_PARTS = 2


def _check_name(name: str) -> None:
    """Refuse a name Django cannot address, which is `app_label.codename`."""
    parts = name.split(".")
    if len(parts) != _NAME_PARTS or not all(parts):
        msg = (
            f"The permission {name!r} is not named 'app_label.codename', the form "
            "user.has_perm() looks permissions up by."
        )
        raise ValueError(msg)


class PermissionRegistry:
    """Hold one predicate per permission name, in the order the names arrived."""

    def __init__(self) -> None:
        """Start empty."""
        self._names: list[str] = []
        self._index: dict[str, Predicate] = {}
        self._version = 0

    @property
    def version(self) -> int:
        """Monotonic counter bumped on every change, a token for derived memos."""
        return self._version

    def add(self, name: str, predicate: Predicate | Test) -> Predicate:
        """Bind `predicate` to the permission `name` and return it as a `Predicate`.

        An equal predicate under the same name is a repeated import and changes nothing.
        """
        _check_name(name)
        rule = predicate if isinstance(predicate, Predicate) else leaf(predicate)
        held = self._index.get(name)
        if held is not None:
            if held == rule:
                return held
            raise PermissionAlreadyRegisteredError(name)
        self._names.append(name)
        self._index[name] = rule
        self._version += 1
        permission_registered.send(sender=PermissionRegistry, name=name, predicate=rule)
        return rule

    def get(self, name: str) -> Predicate | None:
        """Return the predicate of the permission `name`, if it is registered."""
        return self._index.get(name)

    def __contains__(self, name: object) -> bool:
        """Return whether the permission `name` is registered."""
        return name in self._index

    def __len__(self) -> int:
        """Return how many permissions are registered."""
        return len(self._names)

    def items(self) -> Iterator[tuple[str, Predicate]]:
        """Yield each registered name with its predicate, in registration order."""
        for name in self._names:
            yield name, self._index[name]

    def reset(self) -> None:
        """Drop every permission."""
        self._names.clear()
        self._index.clear()
        self._version += 1


__all__ = ["PermissionRegistry"]
