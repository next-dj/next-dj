from typing import ClassVar

from django.db import models


class Placeholder(models.Model):
    """Drop this model and define your own when you fill the template."""

    label = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar = ["-created_at"]

    def __str__(self) -> str:
        return self.label
