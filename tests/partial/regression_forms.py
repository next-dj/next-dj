from django import forms
from django.http import HttpRequest, HttpResponse

from next.forms import Form


class RegressionForm(Form):
    """Form whose invalid POST exercises the unchanged rerender contract."""

    name = forms.CharField(max_length=100)

    def on_valid(self, request: HttpRequest) -> HttpResponse | None:
        """Accept the submission and fall back to the default redirect."""
        return None
