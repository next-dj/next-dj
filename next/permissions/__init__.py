"""Object permissions in Django's words, a predicate per `app_label.codename`.

`user.has_perm(perm, obj)` reaches the predicates through `ObjectPermissionBackend`.
"""

from .backends import ObjectPermissionBackend
from .errors import (
    DeclarationOutsideModuleError,
    PermissionAlreadyRegisteredError,
    UnknownAccessObjectError,
)
from .manager import add_perm, has_perm


__all__ = [
    "DeclarationOutsideModuleError",
    "ObjectPermissionBackend",
    "PermissionAlreadyRegisteredError",
    "UnknownAccessObjectError",
    "add_perm",
    "has_perm",
]
