from typing import Any

from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from shadcn_admin import utils

from next.forms import CharField, Form, PasswordInput, action
from next.pages import context


class AdminLoginForm(Form):
    """Username/password form decoupled from Django's `AuthenticationForm`.

    `AuthenticationForm.__init__` requires `request` as first positional arg,
    which clashes with the dispatcher's `form_class(post_data, ...)` call.
    Authentication is done manually in the action handler instead.
    """

    username = CharField(max_length=150)
    password = CharField(widget=PasswordInput)


@context("login_state")
def login_state(request: HttpRequest) -> dict[str, Any]:
    """Provide an unbound login form to the template."""
    return {
        "form": AdminLoginForm(),
        "next_url": request.GET.get("next", utils.dashboard_url()),
    }


@action("admin:login", form_class=AdminLoginForm)
def admin_login(
    request: HttpRequest,
    form: AdminLoginForm,
) -> HttpResponse:
    """Authenticate the user and redirect to `next` or the dashboard."""
    user = authenticate(
        request,
        username=form.cleaned_data["username"],
        password=form.cleaned_data["password"],
    )
    if user is None:
        messages.error(request, "Invalid username or password.")
        return HttpResponseRedirect(utils.login_url())
    login(request, user)
    messages.success(request, f"Welcome, {user.get_username()}.")
    return HttpResponseRedirect(request.POST.get("next") or utils.dashboard_url())
