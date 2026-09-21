from django import forms
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect

from next.forms import Form
from next.pages import context


@context("secret")
def secret() -> str:
    """Provide the value a refused visitor must never receive."""
    return "classified"


class ShapedNoteForm(Form):
    """Form hosted by the shape-guarded page, submitted from its zone."""

    note = forms.CharField(max_length=10)

    def on_valid(self, request: HttpRequest) -> HttpResponse | None:
        """Accept the note and fall back to the origin re-render."""
        return None


def render(request: HttpRequest) -> str | HttpResponseRedirect:
    """Serve the page only to a GET of its own URL carrying an open token."""
    if request.method != "GET" or request.path != "/shaped/":
        return HttpResponseRedirect("/wrong-shape/")
    if request.GET.get("token") != "open":
        return HttpResponseRedirect("/closed/")
    return "<h1>shaped</h1>"
