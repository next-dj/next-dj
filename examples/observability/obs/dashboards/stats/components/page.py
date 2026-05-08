from obs import metrics

from next.pages import context


@context("counters")
def counters() -> list[tuple[str, int]]:
    """Return ordered `(name, value)` pairs for `components.rendered`."""
    rendered = metrics.read_kind("components.rendered")
    return sorted(rendered.items(), key=lambda item: -item[1])
