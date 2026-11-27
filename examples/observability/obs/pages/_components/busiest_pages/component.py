from obs import metrics

from next import component


component.defer("load", props=("limit",))


@component.context("busiest_pages")
def busiest_pages(limit: int = 5) -> list[tuple[str, int]]:
    """Return the `limit` busiest page modules, read on the widget's own request."""
    return metrics.top_by_kind("pages.rendered", limit=limit)
