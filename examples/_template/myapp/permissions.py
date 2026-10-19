from django.contrib.auth.models import AbstractBaseUser

from myapp.models import Item
from next.permissions import add_perm


def is_signed_in(user: AbstractBaseUser) -> bool:
    """Return whether `user` is signed in, all that adding an item asks."""
    return user.is_authenticated


def is_owner(user: AbstractBaseUser, item: Item | None) -> bool:
    """Return whether `user` owns `item`, false on the model-level question."""
    return item is not None and item.owner_id == user.pk


add_perm("myapp.add_item", is_signed_in)
add_perm("myapp.change_item", is_owner)
add_perm("myapp.delete_item", is_owner)
