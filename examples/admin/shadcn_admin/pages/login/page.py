from typing import Any

from django.contrib.auth import authenticate, login
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect

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

    @classmethod
    def get_initial(cls) -> dict[str, Any]:
        """Return empty initial data; auth check happens in the action handler."""
        return {}


@context("login_state")
def login_state(request: HttpRequest) -> dict[str, Any]:
    """Provide an unbound login form to the template."""
    next_url = request.GET.get("next", "/admin/")
    return {
        "form": AdminLoginForm(),
        "next_url": next_url,
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
        return HttpResponseRedirect("/admin/login/?error=1")
    login(request, user)
    next_url = request.POST.get("next") or "/admin/"
    return HttpResponseRedirect(next_url)
