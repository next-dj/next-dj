from typing import ClassVar, Self

from django.db import models
from django.db.models import Count, Sum, Value
from django.db.models.functions import Coalesce


class PollQuerySet(models.QuerySet):
    def with_totals(self) -> Self:
        """Annotate each poll with its choice count and its total of votes."""
        return self.annotate(
            choice_count=Count("choices", distinct=True),
            total_votes=Coalesce(Sum("choices__votes"), Value(0)),
        )


class Poll(models.Model):
    question = models.CharField(max_length=240)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = PollQuerySet.as_manager()

    class Meta:
        ordering: ClassVar = ["-created_at"]

    def __str__(self) -> str:
        return self.question


class Choice(models.Model):
    poll = models.ForeignKey(Poll, related_name="choices", on_delete=models.CASCADE)
    text = models.CharField(max_length=120)
    votes = models.PositiveIntegerField(default=0)

    class Meta:
        ordering: ClassVar = ["pk"]

    def __str__(self) -> str:
        return self.text
