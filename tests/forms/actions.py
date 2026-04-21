"""Standalone form action registrations used across form tests.

Imported by tests/conftest.py to register actions before Django's URLconf loads.
No pytest imports — this module must be importable without pytest.
"""

from django import forms
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect

from next.forms import Form, action


class SimpleForm(Form):
    """Minimal form used by form action tests."""

    name = forms.CharField(max_length=100)
    email = forms.EmailField(required=False)


class SimpleFormNoEmail(Form):
    """Form without email field."""

    name = forms.CharField(max_length=100)


class SimpleFormWithId(Form):
    """Form with an id field for URL-kwargs tests."""

    name = forms.CharField(max_length=100)
    id = forms.IntegerField()


@action("test_submit", form_class=SimpleForm)
def _test_handler(request: HttpRequest, form: SimpleForm) -> HttpResponse | None:
    return None


@action("test_redirect", form_class=SimpleForm)
def _test_redirect_handler(
    request: HttpRequest, form: SimpleForm
) -> HttpResponseRedirect:
    return HttpResponseRedirect("/done/")


@action("test_no_form")
def _test_no_form_handler(request: HttpRequest) -> HttpResponse:
    return HttpResponse("ok", status=200)
