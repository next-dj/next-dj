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


_EXCERPT_LIMIT = 100


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
        # Position uniqueness inside a column is maintained by move_card
        # and create_card under select_for_update. SQLite does not
        # support deferrable UNIQUE constraints, so the invariant lives
        # at the application layer rather than in the schema.
        ordering: ClassVar = ["position", "id"]

    def __str__(self) -> str:
        """Return the human-readable card title."""
        return self.title

    @property
    def excerpt(self) -> str:
        """Return the card body trimmed for in-card preview."""
        text = self.body or ""
        if len(text) <= _EXCERPT_LIMIT:
            return text
        return text[: _EXCERPT_LIMIT - 1].rstrip() + "…"
