from typing import Any

from django.http import HttpRequest, HttpResponseRedirect
from obs import metrics
from obs.forms import WindowFilterForm
from obs.serializers import PydanticJsContextSerializer

from next.forms import action
from next.pages import context


@context("window", inherit_context=True)
def window(request: HttpRequest = None) -> str:
    """Return the active aggregation window, propagated to nested pages."""
    if request is None:
        return "5m"
    return request.GET.get("window", "5m")


@context(
    "live_stats",
    inherit_context=True,
    serialize=True,
    serializer=PydanticJsContextSerializer(),
)
def live_stats(window: str = "5m") -> dict[str, Any]:
    """Build the snapshot exposed under `window.Next.context.live_stats`.

    The override on this decorator demonstrates that a single key can
    pick a custom serializer even when a different one is wired
    globally. The two happen to coincide here, which is the easiest way
    to keep the page in one piece while still exercising the override
    code path end to end.
    """
    pages = metrics.read_kind("pages.rendered")
    components = metrics.read_kind("components.rendered")
    actions = metrics.read_kind("forms.action_dispatched")
    return {
        "window": window,
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
