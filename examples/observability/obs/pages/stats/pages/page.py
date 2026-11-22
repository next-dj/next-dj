from obs import metrics

from next import context, page
from next.pages import MetadataDict


metadata: MetadataDict = {"title": "Page renders"}

page.defer(timeout="3s")


@context("rows")
def rows() -> list[tuple[str, int, float | None]]:
    """Return each page module, busiest first, with its renders and mean render time.

    The mean raises `CounterLostError` once the cache culled half of a pair.
    """
    means = dict(metrics.latency_by_page())
    counts = metrics.top_by_kind(metrics.RENDERED_KIND)
    return [(module, count, means.get(module)) for module, count in counts]
