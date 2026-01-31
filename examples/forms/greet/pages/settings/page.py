from django import forms
from django.contrib import messages
from django.http import HttpRequest, HttpResponseRedirect

from next.forms import action


class SettingsForm(forms.Form):
    """Settings form: username and optional password."""

    username = forms.CharField(max_length=100)
    password = forms.CharField(
        widget=forms.PasswordInput,
        max_length=100,
        required=False,
    )


def _initial_settings(request: HttpRequest) -> dict:
    """Initial data for settings form from session."""
    return {
        "username": request.session.get("username", ""),
    }


@action("settings", form_class=SettingsForm, initial=_initial_settings)
def settings_handler(request: HttpRequest, form: SettingsForm) -> HttpResponseRedirect:
    """Update session username (and optionally password), add message, redirect to home."""
    request.session["username"] = form.cleaned_data["username"]
    if form.cleaned_data.get("password"):
        request.session["password"] = form.cleaned_data["password"]
    messages.success(request, "Settings saved.")
    return HttpResponseRedirect("/home/")
