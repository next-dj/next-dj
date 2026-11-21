from obs import metrics

from next import component


component.defer("load")


@component.context("busiest_pages")
def busiest_pages() -> list[tuple[str, int]]:
    """Return the five most rendered page modules, read on the widget's own request."""
    return metrics.top_by_kind("pages.rendered", limit=5)
