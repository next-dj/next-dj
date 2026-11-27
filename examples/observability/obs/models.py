from typing import Any

from django.db import models
from django.db.models import Count, Sum
from django.db.models.functions import TruncMinute


class MetricSnapshotQuerySet(models.QuerySet):
    def flushes(self, limit: int) -> list[dict[str, Any]]:
        """Return the latest `limit` minutes a flush ran in, with counters and total."""
        minutes = self.annotate(minute=TruncMinute("captured_at")).values("minute")
        totals = minutes.annotate(counters=Count("pk"), total=Sum("value"))
        return list(totals.order_by("-minute")[:limit])


class MetricSnapshot(models.Model):
    """One persisted counter row produced by `flush_metrics`."""

    kind = models.CharField(max_length=64)
    key = models.CharField(max_length=200)
    value = models.PositiveIntegerField()
    captured_at = models.DateTimeField(auto_now_add=True)

    objects = MetricSnapshotQuerySet.as_manager()

    class Meta:
        ordering = ("-captured_at", "kind", "key")
        unique_together = (("kind", "key", "captured_at"),)

    def __str__(self) -> str:
        return f"{self.kind}:{self.key}={self.value}"
