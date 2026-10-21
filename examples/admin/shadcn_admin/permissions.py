from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

from next.permissions import add_perm


type Visitor = AbstractBaseUser | AnonymousUser


def is_staff(user: Visitor) -> bool:
    """Return whether `user` works the admin, the one question every admin page asks."""
    return user.is_staff


add_perm("shadcn_admin.use_admin", is_staff)
