from __future__ import annotations

from typing import ClassVar

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models


SLUG_RE = r"^[a-z0-9][a-z0-9-]*$"
RESERVED_SLUGS = frozenset({"docs", "articles", "search", "wiki"})


class Article(models.Model):
    """One DB-backed wiki article served at ``/wiki/<slug>/``."""

    SLUG_VALIDATOR = RegexValidator(
        SLUG_RE,
        message="Lowercase letters, digits, and dashes only.",
    )
    slug = models.SlugField(max_length=80, unique=True, validators=[SLUG_VALIDATOR])
    title = models.CharField(max_length=200)
    body_md = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar = ["-updated_at"]

    def __str__(self) -> str:
        """Human-friendly representation that uses the title."""
        return self.title

    def clean(self) -> None:
        """Reject slugs that collide with file-route prefixes."""
        super().clean()
        if self.slug in RESERVED_SLUGS:
            raise ValidationError({"slug": "This slug is reserved by a file route."})

    @property
    def url(self) -> str:
        """Absolute URL of the published article."""
        return f"/wiki/{self.slug}/"
