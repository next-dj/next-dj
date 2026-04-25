from typing import ClassVar

from django.db import models


class Flag(models.Model):
    name = models.SlugField(unique=True, max_length=64)
    label = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar = ["name"]

    def __str__(self) -> str:
        state = "on" if self.enabled else "off"
        return f"{self.name} [{state}]"
