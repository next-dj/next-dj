from django import forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect

from next.forms import Form


WINDOW_CHOICES = (
    ("1m", "Last minute"),
    ("5m", "Last 5 minutes"),
    ("1h", "Last hour"),
)
DEFAULT_WINDOW = "5m"


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

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Persist the picked window via the querystring and redirect back."""
        # Pick the literal out of WINDOW_CHOICES so the redirect target is
        # built from trusted constants, with request data used only to compare.
        chosen = next(
            value for value, _ in WINDOW_CHOICES if value == self.cleaned_data["window"]
        )
        return HttpResponseRedirect(f"/stats/?window={chosen}")
