from obs import metrics

from next import component


@component.context("latencies")
def latencies() -> list[tuple[str, float]]:
    """Return the five pages with the slowest mean render time."""
    return metrics.latency_by_page(limit=5)
