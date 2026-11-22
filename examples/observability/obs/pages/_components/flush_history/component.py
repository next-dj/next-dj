from typing import Any

from obs.models import MetricSnapshot

from next import component


component.defer("manual")

HISTORY_LIMIT = 10


@component.context("flushes")
def flushes() -> list[dict[str, Any]]:
    """Return the latest minutes `flush_metrics` ran in, read on the widget's fetch."""
    return MetricSnapshot.objects.flushes(HISTORY_LIMIT)
