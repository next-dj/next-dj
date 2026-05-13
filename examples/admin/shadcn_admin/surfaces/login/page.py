from typing import Any

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from shadcn_admin import utils

from next.forms import action
from next.pages import context


def admin_login_form_factory(
    request: HttpRequest,
) -> tuple[type[AuthenticationForm], dict[str, Any]]:
    """Bind Django's `AuthenticationForm` with the current request kwarg."""
    return AuthenticationForm, {"request": request}


@context("login_state")
def login_state(request: HttpRequest) -> dict[str, Any]:
    """Provide an unbound login form to the template."""
    return {
        "form": AuthenticationForm(request),
        "next_url": request.GET.get("next", utils.dashboard_url()),
    }


@action("admin:login", form_class=admin_login_form_factory)
def admin_login(
    request: HttpRequest,
    form: AuthenticationForm,
) -> HttpResponse:
    """Authenticate via `AuthenticationForm.get_user()` and redirect."""
    user = form.get_user()
    if user is None:
        messages.error(request, "Invalid username or password.")
        return HttpResponseRedirect(utils.login_url())
    login(request, user)
    messages.success(request, f"Welcome, {user.get_username()}.")
    return HttpResponseRedirect(request.POST.get("next") or utils.dashboard_url())
