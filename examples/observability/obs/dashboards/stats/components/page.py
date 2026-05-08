from obs import metrics

from next.pages import context


@context("counters")
def counters() -> list[tuple[str, int]]:
    """Return cumulative `(name, value)` pairs for `components.rendered`."""
    return metrics.top_by_kind("components.rendered")
