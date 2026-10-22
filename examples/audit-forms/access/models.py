from typing import ClassVar, Self

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db import models
from django.template.defaultfilters import pluralize


class AccessRequestQuerySet(models.QuerySet):
    def visible_to(self, user: AbstractBaseUser | AnonymousUser) -> Self:
        """Return every request, since the portal has no accounts to narrow them by."""
        return self.all()


class AccessRequest(models.Model):
    full_name = models.CharField(max_length=120)
    email = models.EmailField()
    team = models.CharField(max_length=60)
    project_slug = models.SlugField(max_length=64)
    reason = models.TextField()
    expires_in_days = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=12, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AccessRequestQuerySet.as_manager()

    class Meta:
        ordering: ClassVar = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.full_name} ({self.email}) → {self.project_slug} [{self.status}]"

    def is_filed(self) -> bool:
        """Return whether the backend recorded the dispatch that filed this request."""
        return self.audit_entries.filings().exists()

    @property
    def receipt_number(self) -> str:
        """Return the number the receipt is quoted by, padded so receipts sort."""
        return f"AR-{self.pk:05d}"

    def receipt_lines(self) -> list[tuple[str, str]]:
        """Return the receipt as label and value pairs, the source of both formats."""
        days = self.expires_in_days
        return [
            ("Receipt", self.receipt_number),
            ("Requester", f"{self.full_name} <{self.email}>"),
            ("Team", self.team),
            ("Project", self.project_slug),
            ("Access for", f"{days} day{pluralize(days)}"),
            ("Status", self.status),
            ("Filed", self.created_at.strftime("%Y-%m-%d %H:%M UTC")),
            ("Reason", self.reason),
        ]

    def receipt_text(self) -> str:
        """Return the receipt as aligned plain text, the body of the `.txt` download."""
        lines = self.receipt_lines()
        width = max(len(label) for label, _ in lines) + 2
        rows = [f"{label:<{width}}{value}" for label, value in lines]
        return "\n".join(["ACCESS REQUEST RECEIPT", "", *rows, ""])


class AuditEntryQuerySet(models.QuerySet):
    def filings(self) -> Self:
        """Return the backend rows of a final dispatch, the ones linked to a request."""
        return self.filter(
            source=AuditEntry.SOURCE_BACKEND,
            kind=AuditEntry.KIND_DISPATCHED,
            request__isnull=False,
        )


class AuditEntry(models.Model):
    KIND_DISPATCHED = "dispatched"
    KIND_VALIDATION_FAILED = "validation_failed"
    KIND_REQUEST_STARTED = "request_started"
    KIND_ACCESS_DENIED = "access_denied"
    KIND_CHOICES: ClassVar = [
        (KIND_DISPATCHED, "dispatched"),
        (KIND_VALIDATION_FAILED, "validation_failed"),
        (KIND_REQUEST_STARTED, "request_started"),
        (KIND_ACCESS_DENIED, "access_denied"),
    ]

    SOURCE_BACKEND = "backend"
    SOURCE_SIGNAL = "signal"
    SOURCE_CHOICES: ClassVar = [(SOURCE_BACKEND, "backend"), (SOURCE_SIGNAL, "signal")]

    action_name = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES)
    request = models.ForeignKey(
        "access.AccessRequest",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    step = models.CharField(max_length=20, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    duration_ms = models.FloatField(null=True, blank=True)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    error_count = models.PositiveSmallIntegerField(null=True, blank=True)
    field_names = models.JSONField(default=list, blank=True)
    access_layer = models.CharField(max_length=10, blank=True)
    access_reason = models.CharField(max_length=10, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AuditEntryQuerySet.as_manager()

    class Meta:
        ordering: ClassVar = ["-created_at"]

    def __str__(self) -> str:
        when = self.created_at.strftime("%Y-%m-%d %H:%M:%S")
        return f"{when} {self.source}/{self.kind} {self.action_name}"
