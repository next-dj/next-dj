from __future__ import annotations

from typing import ClassVar

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils.text import Truncator


SLUG_RE = r"^[a-z0-9][a-z0-9-]*$"
RESERVED_SLUGS = frozenset({"docs", "articles", "search", "wiki"})
SUMMARY_CHARS = 160
INLINE_MARKUP = str.maketrans("", "", "*_`")


class Article(models.Model):
    """One DB-backed wiki article served at ``/wiki/<slug>/``."""

    SLUG_VALIDATOR = RegexValidator(
        SLUG_RE, message="Lowercase letters, digits, and dashes only."
    )
    slug = models.SlugField(max_length=80, unique=True, validators=[SLUG_VALIDATOR])
    title = models.CharField(max_length=200)
    body_md = models.TextField(blank=True)
    locked = models.BooleanField(default=False)
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
    def summary(self) -> str:
        """Return the first paragraph of the body as plain text, cut to a snippet."""
        for block in self.body_md.split("\n\n"):
            text = " ".join(block.split()).translate(INLINE_MARKUP)
            if text and not text.startswith("#"):
                return Truncator(text).chars(SUMMARY_CHARS)
        return ""

    @property
    def url(self) -> str:
        """Absolute URL of the published article."""
        return f"/wiki/{self.slug}/"
