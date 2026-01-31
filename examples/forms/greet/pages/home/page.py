from django.http import HttpRequest, HttpResponseRedirect

from next.forms import action
from next.pages import context


@context("username")
def get_username(request: HttpRequest) -> str:
    """Provide username from session for template."""
    return request.session.get("username", "")


@action("logout")
def logout_handler(request: HttpRequest) -> HttpResponseRedirect:
    """Clear session and redirect to login page."""
    request.session.flush()
    return HttpResponseRedirect("/login/")
