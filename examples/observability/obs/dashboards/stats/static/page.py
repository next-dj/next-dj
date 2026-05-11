from obs import metrics

from next.pages import context


@context("dedup")
def dedup() -> list[tuple[str, int]]:
    """Return per-kind dedup hit counts."""
    return metrics.top_by_kind("static.dedup")


@context("assets")
def assets() -> list[tuple[str, int]]:
    """Return per-kind total asset key generations."""
    return metrics.top_by_kind("static.asset")


@context("collector")
def collector() -> dict[str, int]:
    """Return cumulative html-injection counters."""
    raw = metrics.read_kind("static")
    return {
        "html_injected": raw.get("html_injected", 0),
        "injected_bytes_total": raw.get("injected_bytes_total", 0),
        "collector_finalized": raw.get("collector_finalized", 0),
    }
