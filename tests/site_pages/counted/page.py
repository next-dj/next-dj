from django.http import HttpRequest

from next.pages import context
from next.partial import zone_requested
from tests.site_pages.counted.probe import counters


# Counters live in the singleton `probe`, and a marker counts only when stringified.


class CountingMarker:
    """Context value that counts its own zone-body interpolation."""

    def __init__(self, name: str) -> None:
        """Bind the marker to the counter key it bumps on render."""
        self.name = name

    def __str__(self) -> str:
        """Bump the per-zone counter as the zone body interpolates the value."""
        counters[self.name] += 1
        return f"{self.name}-body"


@context("alpha_marker")
def alpha_marker() -> CountingMarker:
    """Provide the alpha zone body its render-counting marker."""
    return CountingMarker("alpha")


@context("beta_marker")
def beta_marker() -> CountingMarker:
    """Provide the beta zone body its render-counting marker."""
    return CountingMarker("beta")


@context("gamma_marker")
def gamma_marker() -> CountingMarker:
    """Provide the gamma zone body its render-counting marker."""
    return CountingMarker("gamma")


@context("report")
def report(request: HttpRequest) -> str | None:
    """Touch the database only when the request names the report zone."""
    if not zone_requested(request, "report"):
        return None
    counters["db"] += 1
    return "report-rows"
