from typing import ClassVar

from django.db import models

from notes.themes import DEFAULT_ACCENT, TenantTheme


class Tenant(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    primary_color = models.CharField(max_length=16, default=DEFAULT_ACCENT)
    theme = models.CharField(
        max_length=16, choices=TenantTheme.choices, default=TenantTheme.SHARED
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering: ClassVar = ["slug"]

    def __str__(self) -> str:
        return f"{self.name} <{self.slug}>"


class Note(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="notes")
    title = models.CharField(max_length=160)
    body = models.TextField(blank=True, default="")
    locked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar = ["-updated_at"]

    def __str__(self) -> str:
        return f"{self.tenant.slug}/{self.title}"
