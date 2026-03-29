from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponseRedirect

from next import forms


User = get_user_model()


class RegisterForm(forms.Form):
    """Create a new user account."""

    username = forms.CharField(
        label="Username",
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )
    password2 = forms.CharField(
        label="Password (again)",
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )

    @classmethod
    def get_initial(cls, _request: HttpRequest) -> dict:
        """No default field values for registration."""
        return {}

    def clean_username(self) -> str:
        """Reject taken usernames."""
        username = self.cleaned_data.get("username", "")
        if User.objects.filter(username=username).exists():
            msg = "A user with that username already exists."
            raise ValidationError(msg)
        return username

    def clean_password2(self) -> str:
        """Ensure passwords match and meet validation rules."""
        p1 = self.cleaned_data.get("password1")
        p2 = self.cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            msg = "Passwords do not match."
            raise ValidationError(msg)
        if p1:
            validate_password(p1)
        return p2


@forms.action("register", form_class=RegisterForm)
def register_handler(form: RegisterForm, request: HttpRequest) -> HttpResponseRedirect:
    user = User.objects.create_user(
        username=form.cleaned_data["username"],
        password=form.cleaned_data["password1"],
    )
    login(request, user)
    messages.success(request, "Account created.")
    return HttpResponseRedirect("/")
