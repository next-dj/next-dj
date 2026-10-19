from collections.abc import Generator
from contextlib import contextmanager
from typing import Final

from next.permissions.manager import PermissionManager, permission_manager


MODEL_BACKEND: Final = "django.contrib.auth.backends.ModelBackend"

OBJECT_PERMISSION_BACKENDS: Final = [
    MODEL_BACKEND,
    "next.permissions.ObjectPermissionBackend",
]


def is_owner(user: object, obj: object) -> bool:
    """Return whether `obj` names `user` as its `owner`."""
    return getattr(obj, "owner", None) is user


def is_staff(user: object) -> bool:
    """Return whether `user` is staff, a predicate that never reads the object."""
    return bool(getattr(user, "is_staff", False))


def owns_group(user, obj) -> bool:
    """Return whether the group `obj` carries the username of `user` as its name."""
    return obj is not None and obj.name == user.username


class QuietHolder:
    """A signed-in user who holds the permissions named and records no question.

    A timed loop takes it, so the cost it measures holds no growing record.
    """

    is_authenticated = True

    def __init__(self, *held: str) -> None:
        """Hold the permissions `held`."""
        self.held = frozenset(held)

    def has_perm(self, perm: str, *obj: object) -> bool:
        """Answer the question from the held names alone."""
        return perm in self.held


class PermissionHolder(QuietHolder):
    """A signed-in user who holds the permissions named and records every question."""

    def __init__(self, *held: str) -> None:
        """Hold the permissions `held`, with nothing asked yet."""
        super().__init__(*held)
        self.asked: list[tuple[object, ...]] = []

    def has_perm(self, perm: str, *obj: object) -> bool:
        """Record the question and answer it from the held names alone."""
        self.asked.append((perm, *obj))
        return super().has_perm(perm, *obj)


@contextmanager
def restored_permissions() -> Generator[PermissionManager, None, None]:
    """Run a block over an empty permission registry, then put the earlier one back.

    The model permission memo drops too, so no block reads another app registry.
    """
    held = list(permission_manager.registry.items())
    permission_manager.reset()
    try:
        yield permission_manager
    finally:
        permission_manager.reset()
        for name, rule in held:
            permission_manager.add(name, rule)
