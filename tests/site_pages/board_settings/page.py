from django import forms
from django.contrib import messages
from django.http import HttpRequest, HttpResponse

from next.forms import Form
from next.pages import context


@context("greeting")
def greeting() -> str:
    """Provide a value the zone body reads beside the bound form."""
    return "hi"


class ZonedRenameForm(Form):
    """Form that lives inside a zone so its invalid submit morphs the zone.

    Meta.success_message flashes a contrib message, so a partial success
    funnel drains it into a toast patch.
    """

    title = forms.CharField(max_length=100)

    class Meta:
        """Flash a success message on a valid rename."""

        success_message = "Board renamed"

    def on_valid(self, request: HttpRequest) -> HttpResponse | None:
        """Accept the rename and fall back to the default redirect."""
        messages.info(request, "secondary note")
        return None
