from django import forms
from django.http import HttpRequest, HttpResponseRedirect

from next.forms import action


LOGIN_USERNAME = "form"
LOGIN_PASSWORD = "form"


class LoginForm(forms.Form):
    """Login form: username and password. Validates credentials in clean()."""

    username = forms.CharField(max_length=100)
    password = forms.CharField(widget=forms.PasswordInput, max_length=100)

    def clean(self) -> dict:
        """Validate username and password are form/form."""
        data = super().clean()
        username = data.get("username")
        password = data.get("password")
        if username != LOGIN_USERNAME or password != LOGIN_PASSWORD:
            msg = "Invalid username or password."
            raise forms.ValidationError(msg)
        return data


def _initial_login(_request: HttpRequest) -> dict:
    """Initial data for login form (empty)."""
    return {}


@action("login", form_class=LoginForm, initial=_initial_login)
def login_handler(request: HttpRequest, form: LoginForm) -> HttpResponseRedirect:
    """Set session username and redirect to /home/. Form valid only when credentials match."""
    request.session["username"] = form.cleaned_data["username"]
    return HttpResponseRedirect("/home/")
