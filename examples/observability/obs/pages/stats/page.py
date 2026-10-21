from typing import Any

from django.http import HttpRequest
from obs import metrics
from obs.forms import DEFAULT_WINDOW
from obs.serializers import WrappedJsContextSerializer

from next import context
from next.pages import MetadataDict


metadata: MetadataDict = {"title": "Live stats"}

# Page level, not next to the widget, because it must load first.
scripts = [
    "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js",
    "dashboards/js/chart_theme.js",
]

WINDOW_TO_MINUTES = {"1m": 1, "5m": 5, "1h": 60}
LIVE_TOTALS_ZONE = "live-totals"


def _minutes_for(window: str) -> int:
    return WINDOW_TO_MINUTES.get(window, WINDOW_TO_MINUTES[DEFAULT_WINDOW])


@context("live_zone")
def live_zone() -> str:
    """Name the zone the filter form re-aggregates without leaving the page.

    Page-local, so nested stats pages keep it empty and fall back to a full submit.
    """
    return LIVE_TOTALS_ZONE


@context("window", inherit_context=True)
def window(request: HttpRequest | None = None) -> str:
    """Return the active aggregation window, propagated to nested pages.

    The `HttpRequest | None` default lets unit calls skip DI.
    """
    if request is None:
        return DEFAULT_WINDOW
    return request.POST.get("window") or request.GET.get("window", DEFAULT_WINDOW)


@context(
    "live_stats",
    inherit_context=True,
    serialize=True,
    serializer=WrappedJsContextSerializer(),
)
def live_stats(window: str = DEFAULT_WINDOW) -> dict[str, Any]:
    """Build the windowed snapshot exposed under `window.Next.context.live_stats`.

    Its decorator wraps the payload in `{"v": 1, "data": ...}`, sibling keys stay flat.
    """
    minutes = _minutes_for(window)
    pages = metrics.read_window("pages.rendered", minutes)
    components = metrics.read_window("components.rendered", minutes)
    actions = metrics.read_window("forms.action_dispatched", minutes)
    return {
        "window": window,
        "minutes": minutes,
        "totals": {
            "pages": sum(pages.values()),
            "components": sum(components.values()),
            "actions": sum(actions.values()),
        },
    }
