from django.db import models


class MetricSnapshot(models.Model):
    """One persisted counter row produced by `flush_metrics`."""

    kind = models.CharField(max_length=64)
    key = models.CharField(max_length=200)
    value = models.PositiveIntegerField()
    captured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-captured_at", "kind", "key")
        unique_together = (("kind", "key", "captured_at"),)

    def __str__(self) -> str:
        return f"{self.kind}:{self.key}={self.value}"
