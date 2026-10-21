from typing import ClassVar, Self

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db import models


class ItemQuerySet(models.QuerySet):
    def visible_to(self, user: AbstractBaseUser | AnonymousUser) -> Self:
        """Return the items `user` owns, every item for a superuser."""
        if not user.is_authenticated:
            return self.none()
        if user.is_superuser:
            return self
        return self.filter(owner=user)


class Item(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="myapp_items"
    )
    title = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ItemQuerySet.as_manager()

    class Meta:
        ordering: ClassVar = ["-created_at"]
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=~models.Q(title=""),
                name="myapp_item_title_not_empty",
                violation_error_message="An item needs a title.",
            )
        ]

    def __str__(self) -> str:
        return self.title
