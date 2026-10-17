"""Object permissions in Django's words, a predicate per `app_label.codename`.

`user.has_perm(perm, obj)` reaches the predicates through `ObjectPermissionBackend`.
"""

from . import signals
from .access import check, declared
from .backends import ObjectPermissionBackend
from .errors import PermissionAlreadyRegisteredError, UnknownAccessObjectError
from .manager import add_perm, has_perm, perms_for
from .markers import AccessRule, Denial, Predicate, Requirement, predicate


__all__ = [
    "AccessRule",
    "Denial",
    "ObjectPermissionBackend",
    "PermissionAlreadyRegisteredError",
    "Predicate",
    "Requirement",
    "UnknownAccessObjectError",
    "add_perm",
    "check",
    "declared",
    "has_perm",
    "perms_for",
    "predicate",
    "signals",
]
