from django import forms as django_forms

from next.forms import Form


WINDOW_CHOICES = (
    ("1m", "Last minute"),
    ("5m", "Last 5 minutes"),
    ("1h", "Last hour"),
)


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
