from obs import metrics

from next.components import component


@component.context("busiest_pages")
def busiest_pages() -> list[tuple[str, int]]:
    """Return the five most rendered page modules for the lazy widget."""
    return metrics.top_by_kind("pages.rendered", limit=5)
