from django import forms
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect

from next.forms import Form, action


class SimpleForm(Form):
    """Minimal form used by form action tests."""

    name = forms.CharField(max_length=100)
    email = forms.EmailField(required=False)

    def on_valid(self, request: HttpRequest) -> HttpResponse | None:
        """Accept the submission and return None to trigger default redirect."""
        return None


class SimpleFormRedirect(Form):
    """Form that redirects on submission."""

    name = forms.CharField(max_length=100)

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Redirect to /done/ on valid submission."""
        return HttpResponseRedirect("/done/")


class SimpleFormNoEmail(Form):
    """Form without email field."""

    name = forms.CharField(max_length=100)


class SimpleFormWithId(Form):
    """Form with an id field for URL-kwargs tests."""

    name = forms.CharField(max_length=100)
    id = forms.IntegerField()


@action("test_no_form")
def test_no_form_handler(request: HttpRequest) -> HttpResponse:
    return HttpResponse("ok", status=200)
