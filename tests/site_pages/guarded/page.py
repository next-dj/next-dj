from django import forms
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect

from next.forms import Form
from next.pages import context


@context("secret")
def secret() -> str:
    """Provide the value a denied visitor must never receive."""
    return "classified"


class GuardedNoteForm(Form):
    """Form hosted by the guarded page, submitted from the guarded zone."""

    note = forms.CharField(max_length=10)

    def on_valid(self, request: HttpRequest) -> HttpResponse | None:
        """Accept the note and fall back to the origin re-render."""
        return None


def render(request: HttpRequest) -> str | HttpResponseRedirect:
    """Serve the page body, sending an anonymous visitor to the login page."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return HttpResponseRedirect("/login/")
    return "<h1>guarded</h1>"
