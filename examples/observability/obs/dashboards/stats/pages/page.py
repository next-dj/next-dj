from obs import metrics

from next.pages import context


@context("counters")
def counters() -> list[tuple[str, int]]:
    """Return cumulative `(file_path, value)` pairs for the `pages.rendered` kind."""
    return metrics.top_by_kind("pages.rendered")
