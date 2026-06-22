from django import forms as django_forms
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect

from next.forms import Form
from next.partial import Patches, is_partial_request


WINDOW_CHOICES = (
    ("1m", "Last minute"),
    ("5m", "Last 5 minutes"),
    ("1h", "Last hour"),
)
DEFAULT_WINDOW = "5m"
LIVE_TOTALS_ZONE = "live-totals"
METRIC_PULSE_OP = "metric-pulse"


class WindowFilterForm(Form):
    """Pick the time window the dashboard aggregates over."""

    window = django_forms.ChoiceField(
        choices=WINDOW_CHOICES,
        widget=django_forms.Select(
            attrs={
                "class": (
                    "rounded-md border border-slate-300 bg-white px-3 py-2 "
                    "text-sm focus:outline-none focus:ring-2 focus:ring-slate-400"
                ),
            },
        ),
    )

    @classmethod
    def get_initial(cls, request: HttpRequest) -> dict[str, str]:
        """Seed the select with the window the dashboard currently shows."""
        return {"window": request.GET.get("window", DEFAULT_WINDOW)}

    def on_valid(self, request: HttpRequest) -> HttpResponse:
        """Re-aggregate the totals under the picked window and pulse the change.

        A partial apply from the live page morphs the `live-totals` zone
        with the re-aggregated cards and emits the custom `metric-pulse`
        verb so the co-located handler flashes the refreshed numbers.
        Without the runtime the apply falls back to a redirect that carries
        the window in the querystring.
        """
        # Pick the literal out of WINDOW_CHOICES so the redirect target is
        # built from trusted constants, with request data used only to compare.
        chosen = next(
            value for value, _ in WINDOW_CHOICES if value == self.cleaned_data["window"]
        )
        if not is_partial_request(request):
            return HttpResponseRedirect(f"/stats/?window={chosen}")
        return (
            Patches(request)
            .morph(zone=LIVE_TOTALS_ZONE)
            .op(METRIC_PULSE_OP, window=chosen, selector="[data-metric-pulse-target]")
            .response()
        )
