"""Object permissions in Django's words, a predicate per `app_label.codename`.

`user.has_perm(perm, obj)` reaches the predicates through `ObjectPermissionBackend`.
"""

from . import checks, signals
from .backends import ObjectPermissionBackend
from .errors import PermissionAlreadyRegisteredError
from .manager import add_perm, has_perm, perms_for
from .markers import Predicate, predicate


__all__ = [
    "ObjectPermissionBackend",
    "PermissionAlreadyRegisteredError",
    "Predicate",
    "add_perm",
    "checks",
    "has_perm",
    "perms_for",
    "predicate",
    "signals",
]
