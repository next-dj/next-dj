from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponseRedirect

from next import forms


class LoginForm(forms.Form):
    """Authenticate with username and password."""

    username = forms.CharField(
        label="Username",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )

    @classmethod
    def get_initial(cls, _request: HttpRequest) -> dict:
        """No default field values for login."""
        return {}

    def clean(self) -> dict:
        """Validate credentials and attach authenticated_user."""
        cleaned = super().clean()
        username = cleaned.get("username")
        password = cleaned.get("password")
        if username is None or password is None:
            return cleaned
        user = authenticate(username=username, password=password)
        if user is None:
            msg = "Invalid username or password."
            raise ValidationError(msg)
        cleaned["authenticated_user"] = user
        return cleaned


@forms.action("login", form_class=LoginForm)
def login_handler(form: LoginForm, request: HttpRequest) -> HttpResponseRedirect:
    user = form.cleaned_data["authenticated_user"]
    login(request, user)
    messages.success(request, "Signed in.")
    next_url = request.POST.get("next") or "/"
    if not next_url.startswith("/"):
        next_url = "/"
    return HttpResponseRedirect(next_url)
