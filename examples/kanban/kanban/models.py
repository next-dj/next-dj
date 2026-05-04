from __future__ import annotations

from typing import ClassVar

from django.db import models


class Board(models.Model):
    """A Kanban board owns an ordered list of columns and metadata."""

    title = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar = ["-created_at"]

    def __str__(self) -> str:
        """Return the human-readable board title."""
        return self.title


class Column(models.Model):
    """A vertical lane on a board where cards are stacked in display order."""

    board = models.ForeignKey(
        Board,
        on_delete=models.CASCADE,
        related_name="columns",
    )
    title = models.CharField(max_length=120)
    position = models.PositiveIntegerField(default=0)
    wip_limit = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering: ClassVar = ["position", "id"]
        unique_together: ClassVar = [("board", "position")]

    def __str__(self) -> str:
        """Return the human-readable column title."""
        return self.title


class Card(models.Model):
    """An individual task card that lives inside one column."""

    column = models.ForeignKey(
        Column,
        on_delete=models.CASCADE,
        related_name="cards",
    )
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar = ["position", "id"]

    def __str__(self) -> str:
        """Return the human-readable card title."""
        return self.title
