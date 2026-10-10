from obs import metrics

from next import context
from next.pages import MetadataDict


metadata: MetadataDict = {"title": "Component renders"}


@context("counters")
def counters() -> list[tuple[str, int]]:
    """Return cumulative `(name, value)` pairs for `components.rendered`."""
    return metrics.top_by_kind("components.rendered")
