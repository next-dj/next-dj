from django.http import Http404
from obs import metrics
from obs.forms import WINDOW_CHOICES

from next import context, page
from next.pages import MetadataDict


metadata: MetadataDict = {"title": "Page renders"}

page.defer(timeout="3s")


@context("known_window", guard=True)
def known_window(window: str) -> str:
    """Answer 404 for a window the stats tabs do not offer, before the shell goes out.

    A guard keeps the honest status of a page whose body streams behind `loading.djx`.
    """
    if window not in dict(WINDOW_CHOICES):
        raise Http404(window)
    return window


@context("rows")
def rows() -> list[tuple[str, int, float | None]]:
    """Return each page module, busiest first, with its renders and mean render time.

    The mean raises `CounterLostError` once the cache culled half of a pair.
    """
    means = dict(metrics.latency_by_page())
    counts = metrics.top_by_kind(metrics.RENDERED_KIND)
    return [(module, count, means.get(module)) for module, count in counts]
