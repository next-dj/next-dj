from typing import ClassVar

from django.conf import settings
from django.db import models


class AdminActivityLog(models.Model):
    """One row per `action_dispatched` for any `admin:*` action.

    Records the action name, target model, object repr, response status,
    and the user when the dispatched action carries a form (so an admin
    form factory could attach the spec). Form-less actions (`admin:delete`,
    `admin:bulk_action`) leave `user` empty because the signal payload has
    no request reference.
    """

    ACTION_ADD = "add"
    ACTION_CHANGE = "change"
    ACTION_DELETE = "delete"
    ACTION_BULK = "bulk_action"

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_activity",
    )
    action = models.CharField(max_length=32)
    app_label = models.CharField(max_length=64, blank=True)
    model_name = models.CharField(max_length=64, blank=True)
    object_repr = models.CharField(max_length=200, blank=True)
    response_status = models.PositiveSmallIntegerField()

    class Meta:
        ordering: ClassVar = ["-timestamp"]
        verbose_name = "admin activity"
        verbose_name_plural = "admin activity"

    def __str__(self) -> str:  # pragma: no cover
        target = f"{self.app_label}.{self.model_name}" if self.app_label else "-"
        return f"{self.timestamp:%Y-%m-%d %H:%M:%S} {self.action} {target}"
