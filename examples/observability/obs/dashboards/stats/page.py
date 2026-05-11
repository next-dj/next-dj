from typing import Any

from django.http import HttpRequest, HttpResponseRedirect
from obs import metrics
from obs.forms import WindowFilterForm
from obs.serializers import WrappedJsContextSerializer

from next.forms import action
from next.pages import context


WINDOW_TO_MINUTES = {"1m": 1, "5m": 5, "1h": 60}
DEFAULT_WINDOW = "5m"


def _minutes_for(window: str) -> int:
    return WINDOW_TO_MINUTES.get(window, WINDOW_TO_MINUTES[DEFAULT_WINDOW])


@context("window", inherit_context=True)
def window(request: HttpRequest | None = None) -> str:
    """Return the active aggregation window, propagated to nested pages.

    The `HttpRequest | None` annotation matches the union form
    `HttpRequestProvider` accepts, so DI fills the parameter on every
    real render while direct unit-test calls keep working with the
    default `None`.
    """
    if request is None:
        return DEFAULT_WINDOW
    return request.GET.get("window", DEFAULT_WINDOW)


@context(
    "live_stats",
    inherit_context=True,
    serialize=True,
    serializer=WrappedJsContextSerializer(),
)
def live_stats(window: str = DEFAULT_WINDOW) -> dict[str, Any]:
    """Build the windowed snapshot exposed under `window.Next.context.live_stats`.

    The override on this decorator wraps the payload in
    `{"v": 1, "data": ...}`. Sibling serialised keys stay flat through
    the global `JS_CONTEXT_SERIALIZER`, so the difference is visible
    end to end in the rendered HTML.

    Counts are read through `metrics.read_window` so each ``?window=``
    setting actually narrows the aggregation. Cumulative tables on
    `/stats/pages/` and `/stats/components/` continue to use the
    process-lifetime totals from `metrics.read_kind`.
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


@action("filter_window", namespace="obs", form_class=WindowFilterForm)
def filter_window(form: WindowFilterForm) -> HttpResponseRedirect:
    """Persist the picked window via the querystring and redirect back."""
    chosen = form.cleaned_data["window"]
    return HttpResponseRedirect(f"/stats/?window={chosen}")
