from django.db import models


class MetricSnapshot(models.Model):
    """One persisted counter row produced by `flush_metrics`.

    The triple `(kind, key, captured_at)` is unique. The kind groups
    counters by signal family (for example `pages` or `static.dedup`)
    and the key is the per-event identifier inside that family.
    """

    kind = models.CharField(max_length=64)
    key = models.CharField(max_length=200)
    value = models.PositiveIntegerField()
    captured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-captured_at", "kind", "key")
        unique_together = (("kind", "key", "captured_at"),)

    def __str__(self) -> str:
        return f"{self.kind}:{self.key}={self.value}"
