from typing import ClassVar, Self

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db import models


class AdminActivityLogQuerySet(models.QuerySet):
    def visible_to(self, user: AbstractBaseUser | AnonymousUser) -> Self:
        """Return every entry to a user who may read the audit log, and none else."""
        if user.has_perm("admin_audit.view_adminactivitylog"):
            return self.all()
        return self.none()

    def recent(self, limit: int) -> "AdminActivityLogQuerySet":
        """Return the newest `limit` entries with their users, newest first."""
        return self.select_related("user")[:limit]


class AdminActivityLog(models.Model):
    """One row per `action_dispatched` for any `admin:*` action."""

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

    objects = AdminActivityLogQuerySet.as_manager()

    class Meta:
        ordering: ClassVar = ["-timestamp"]
        verbose_name = "admin activity"
        verbose_name_plural = "admin activity"

    def __str__(self) -> str:  # pragma: no cover
        target = f"{self.app_label}.{self.model_name}" if self.app_label else "-"
        return f"{self.timestamp:%Y-%m-%d %H:%M:%S} {self.action} {target}"
