"""The object permissions of a project and the answers a backend gives about them.

Nothing here imports a model, so the area loads before the app registry is ready.
"""

from collections.abc import Callable, Mapping
from typing import Final

from django.apps import apps
from django.conf import settings
from django.utils.module_loading import import_string

from .access import auth_installed
from .markers import Test
from .registry import PermissionRegistry


_NEIGHBOURS: Final[Mapping[str, str]] = {
    "rules.permissions.ObjectPermissionBackend": "rules.permissions.perm_exists"
}


class PermissionManager:
    """Registers object permissions and answers `has_perm` questions about them."""

    def __init__(self, registry: PermissionRegistry | None = None) -> None:
        """Start with a registry of its own unless given one."""
        self._registry = registry if registry is not None else PermissionRegistry()
        self._models: tuple[object, frozenset[str]] | None = None

    @property
    def registry(self) -> PermissionRegistry:
        """Return the registry the manager reads and writes."""
        return self._registry

    @property
    def version(self) -> int:
        """Return the counter the registry bumps on every change."""
        return self._registry.version

    def add(self, name: str, predicate: Test) -> None:
        """Register `predicate` under the permission `name`."""
        self._registry.add(name, predicate)

    def is_registered(self, perm: str) -> bool:
        """Return whether `perm` is registered here."""
        return perm in self._registry

    def has_perm(self, user: object, perm: str, obj: object = None) -> bool:
        """Return whether an active `user` holds the registered `perm` on `obj`.

        An inactive user holds nothing, the rule Django's `ModelBackend` follows.
        """
        if not getattr(user, "is_active", False):
            return False
        rule = self._registry.get(perm)
        return rule is not None and rule(user, obj)

    def perms_for(self, user: object, obj: object = None) -> frozenset[str]:
        """Return every registered permission an active `user` holds on `obj`."""
        if not getattr(user, "is_active", False):
            return frozenset()
        return frozenset(
            name for name, rule in self._registry.items() if rule(user, obj)
        )

    def has_module_perms(self, user: object, app_label: str) -> bool:
        """Return whether an active `user` holds any registered perm of `app_label`."""
        if not getattr(user, "is_active", False):
            return False
        prefix = f"{app_label}."
        return any(
            name.startswith(prefix) and rule(user, None)
            for name, rule in self._registry.items()
        )

    def known(self, perm: str) -> bool:
        """Return whether `perm` names a permission something can grant.

        Django's model permissions count, so do the registered ones and a neighbour's.
        """
        if perm in self._registry or perm in self._model_permissions():
            return True
        return any(exists(perm) for exists in _neighbour_checks())

    def _model_permissions(self) -> frozenset[str]:
        """Return the permissions Django creates for the installed models.

        The set is kept while the app registry hands out the same model list.
        """
        if not auth_installed():
            return frozenset()
        models = apps.get_models()
        held = self._models
        if held is not None and held[0] is models:
            return held[1]
        found: set[str] = set()
        for model in models:
            opts = model._meta
            found.update(
                f"{opts.app_label}.{action}_{opts.model_name}"
                for action in opts.default_permissions
            )
            found.update(
                f"{opts.app_label}.{codename}" for codename, _ in opts.permissions
            )
        permissions = frozenset(found)
        self._models = (models, permissions)
        return permissions

    def reset(self) -> None:
        """Drop every registered permission and the model permission memo."""
        self._registry.reset()
        self._models = None


def _class_paths(path: str) -> frozenset[str]:
    """Return the dotted path of the backend at `path` and of every class it inherits.

    A backend that fails to import inherits nothing, which `next.E160` reports.
    """
    try:
        backend = import_string(path)
    except ImportError:
        return frozenset()
    if not isinstance(backend, type):
        return frozenset()
    return frozenset(f"{cls.__module__}.{cls.__qualname__}" for cls in backend.__mro__)


def listed_backends() -> frozenset[str]:
    """Return the dotted path of every listed authentication backend and its bases."""
    listed = settings.AUTHENTICATION_BACKENDS
    return frozenset(listed).union(*(_class_paths(path) for path in listed))


def _neighbour_checks() -> list[Callable[[str], bool]]:
    """Return the existence check of every configured compatible backend.

    A subclass counts, and the match is by name, so no unconfigured neighbour imports.
    """
    configured = listed_backends()
    checks: list[Callable[[str], bool]] = []
    for backend, path in _NEIGHBOURS.items():
        if backend not in configured:
            continue
        try:
            checks.append(import_string(path))
        except ImportError:
            continue
    return checks


permission_manager = PermissionManager()


def add_perm(name: str, predicate: Test) -> None:
    """Register `predicate` as the object permission `name`, `app_label.codename`."""
    permission_manager.add(name, predicate)


def has_perm(user: object, perm: str, obj: object = None) -> bool:
    """Return whether an active `user` holds the registered `perm` on `obj`."""
    return permission_manager.has_perm(user, perm, obj)


__all__ = [
    "PermissionManager",
    "add_perm",
    "has_perm",
    "listed_backends",
    "permission_manager",
]
