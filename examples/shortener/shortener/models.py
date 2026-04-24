from typing import ClassVar

from django.db import models


class Link(models.Model):
    slug = models.SlugField(unique=True, max_length=32)
    url = models.URLField(max_length=2000)
    clicks = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.slug} → {self.url}"
