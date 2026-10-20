from typing import Any, ClassVar

from django import forms
from django.http import HttpRequest, HttpResponse
from django.middleware.csrf import rotate_token

from next.forms import Form, PermissionOutcome


class RegressionForm(Form):
    """Form whose invalid POST exercises the unchanged rerender contract."""

    name = forms.CharField(max_length=100)

    def on_valid(self, request: HttpRequest) -> HttpResponse | None:
        """Accept the submission and fall back to the default redirect."""
        return None


class ValidateForm(Form):
    """Two required fields, a file field, and a cross-field clean.

    They prove validate scrubs file names and drops non-field errors.
    """

    name = forms.CharField(max_length=100)
    email = forms.EmailField()
    avatar = forms.FileField()

    def clean(self) -> dict:
        """Raise a cross-field error so the validate pass can scrub it."""
        super().clean()
        msg = "cross-field never allowed on blur"
        raise forms.ValidationError(msg)

    def on_valid(self, request: HttpRequest) -> HttpResponse | None:
        """Accept the submission and fall back to the default redirect."""
        return None


class GuardedValidateForm(Form):
    """Login-guarded form used to prove validate runs only behind the guard.

    An anonymous validate must reach a denial, never the uniqueness check.
    """

    email = forms.EmailField()

    class Meta:
        """Require an authenticated user for every dispatch."""

        login_required: ClassVar[bool] = True

    def on_valid(self, request: HttpRequest) -> HttpResponse | None:
        """Accept the submission and fall back to the default redirect."""
        return None


class ViewGuardValidateForm(Form):
    """Form guarded by a view-level hook, with no action guard at all.

    The denial must come from the view-permission layer before the form binds.
    """

    email = forms.EmailField()

    @classmethod
    def check_permissions(cls, request: HttpRequest) -> PermissionOutcome:
        """Allow only authenticated users, deny anonymous validate callers."""
        user = getattr(request, "user", None)
        return bool(user is not None and user.is_authenticated)

    def clean_email(self) -> str:
        """Reject a reserved address so a reached validator is observable."""
        value = self.cleaned_data["email"]
        if value == "taken@example.com":
            msg = "address already registered"
            raise forms.ValidationError(msg)
        return value

    def on_valid(self, request: HttpRequest) -> HttpResponse | None:
        """Accept the submission and fall back to the default redirect."""
        return None


class RotatingValidateForm(Form):
    """Form that rotates the CSRF token during initial resolution.

    The validate envelope stamps the fresh CSRF payload only when the request rotated.
    """

    email = forms.EmailField()

    @classmethod
    def get_initial(cls, request: HttpRequest) -> dict[str, Any]:
        """Rotate the request CSRF token before the form binds."""
        rotate_token(request)
        return {}

    def on_valid(self, request: HttpRequest) -> HttpResponse | None:
        """Accept the submission and fall back to the default redirect."""
        return None
