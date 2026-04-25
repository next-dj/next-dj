from typing import ClassVar

from django.db import models


class AccessRequest(models.Model):
    full_name = models.CharField(max_length=120)
    email = models.EmailField()
    team = models.CharField(max_length=60)
    project_slug = models.SlugField(max_length=64)
    reason = models.TextField()
    expires_in_days = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=12, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.full_name} ({self.email}) → {self.project_slug} [{self.status}]"


class AuditEntry(models.Model):
    KIND_DISPATCHED = "dispatched"
    KIND_VALIDATION_FAILED = "validation_failed"
    KIND_REQUEST_STARTED = "request_started"
    KIND_CHOICES: ClassVar = [
        (KIND_DISPATCHED, "dispatched"),
        (KIND_VALIDATION_FAILED, "validation_failed"),
        (KIND_REQUEST_STARTED, "request_started"),
    ]

    SOURCE_BACKEND = "backend"
    SOURCE_SIGNAL = "signal"
    SOURCE_CHOICES: ClassVar = [
        (SOURCE_BACKEND, "backend"),
        (SOURCE_SIGNAL, "signal"),
    ]

    action_name = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES)
    step = models.CharField(max_length=20, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    duration_ms = models.FloatField(null=True, blank=True)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    error_count = models.PositiveSmallIntegerField(null=True, blank=True)
    field_names = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar = ["-created_at"]

    def __str__(self) -> str:
        when = self.created_at.strftime("%Y-%m-%d %H:%M:%S")
        return f"{when} {self.source}/{self.kind} {self.action_name}"
