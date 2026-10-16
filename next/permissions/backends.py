"""The authentication backend that answers `user.has_perm()` from the predicates.

It stays off Django's `BaseBackend`, whose module imports the auth models.
"""

from asgiref.sync import sync_to_async

from .manager import permission_manager


class ObjectPermissionBackend:
    """Grant the registered permissions their predicates allow, authenticating nobody.

    The async twins run the predicates in a thread, since a predicate may query.
    """

    def authenticate(self, *args, **kwargs) -> None:
        """Authenticate nobody, so another backend logs the user in."""

    async def aauthenticate(self, *args, **kwargs) -> None:
        """Authenticate nobody, so another backend logs the user in."""

    def has_perm(self, user_obj: object, perm: str, obj: object = None) -> bool:
        """Return whether the predicate of `perm` allows `user_obj` on `obj`."""
        return permission_manager.has_perm(user_obj, perm, obj)

    async def ahas_perm(self, user_obj: object, perm: str, obj: object = None) -> bool:
        """Return whether the predicate of `perm` allows `user_obj` on `obj`."""
        return await sync_to_async(permission_manager.has_perm)(user_obj, perm, obj)

    def has_module_perms(self, user_obj: object, app_label: str) -> bool:
        """Return whether `user_obj` holds a registered permission of `app_label`."""
        return permission_manager.has_module_perms(user_obj, app_label)

    async def ahas_module_perms(self, user_obj: object, app_label: str) -> bool:
        """Return whether `user_obj` holds a registered permission of `app_label`."""
        return await sync_to_async(permission_manager.has_module_perms)(
            user_obj, app_label
        )

    def get_all_permissions(self, user_obj: object, obj: object = None) -> set[str]:
        """Return every registered permission `user_obj` holds on `obj`."""
        return set(permission_manager.perms_for(user_obj, obj))

    async def aget_all_permissions(
        self, user_obj: object, obj: object = None
    ) -> set[str]:
        """Return every registered permission `user_obj` holds on `obj`."""
        held = await sync_to_async(permission_manager.perms_for)(user_obj, obj)
        return set(held)


__all__ = ["ObjectPermissionBackend"]
