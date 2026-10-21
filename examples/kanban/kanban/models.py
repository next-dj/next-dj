from typing import ClassVar, Self

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db import models


class BoardQuerySet(models.QuerySet):
    def visible_to(self, user: AbstractBaseUser | AnonymousUser) -> Self:
        """Return every board, since the demo has no accounts to narrow them by."""
        return self.all()

    def active(self) -> Self:
        """Return the boards still in use, newest first."""
        return self.filter(archived=False).order_by("-created_at")

    def archived(self) -> Self:
        """Return the boards taken out of the index."""
        return self.filter(archived=True)


class Board(models.Model):
    """A Kanban board owns an ordered list of columns and metadata."""

    title = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = BoardQuerySet.as_manager()

    class Meta:
        ordering: ClassVar = ["-created_at"]

    def __str__(self) -> str:
        """Return the human-readable board title."""
        return self.title

    def card(self, pk: int | str) -> "Card | None":
        """Return the card `pk` names when it sits on this board, with its column."""
        cards = Card.objects.select_related("column").filter(column__board=self)
        return cards.filter(pk=pk).first()


class Column(models.Model):
    """A vertical lane on a board where cards are stacked in display order."""

    board = models.ForeignKey(Board, on_delete=models.CASCADE, related_name="columns")
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

    column = models.ForeignKey(Column, on_delete=models.CASCADE, related_name="cards")
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # SQLite lacks deferrable UNIQUE, so services keep positions unique by a lock.
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
