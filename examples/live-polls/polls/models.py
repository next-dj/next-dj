from typing import ClassVar

from django.db import models


class Poll(models.Model):
    question = models.CharField(max_length=240)
    created_at = models.DateTimeField(auto_now_add=True)

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
