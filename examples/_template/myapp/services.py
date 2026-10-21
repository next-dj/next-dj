from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction

from myapp.models import Item
from myapp.signals import item_created


def create_item(user: AbstractBaseUser, *, title: str) -> Item:
    """Create an item owned by `user` and announce it through `item_created`."""
    with transaction.atomic():
        item = Item(owner=user, title=title)
        item.full_clean()
        item.save()
        item_created.send(sender=Item, item=item, user=user)
    return item


def rename_item(user: AbstractBaseUser, *, item_id: int, title: str) -> Item:
    """Rename an item `user` may see, raising `Item.DoesNotExist` for any other."""
    with transaction.atomic():
        item = Item.objects.visible_to(user).select_for_update().get(pk=item_id)
        item.title = title
        item.full_clean()
        item.save()
    return item
