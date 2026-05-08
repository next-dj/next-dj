from obs import metrics

from next.pages import context


@context("counters")
def counters() -> list[tuple[str, int]]:
    """Return ordered `(file_path, value)` pairs for the `pages.rendered` kind."""
    rendered = metrics.read_kind("pages.rendered")
    return sorted(rendered.items(), key=lambda item: -item[1])
