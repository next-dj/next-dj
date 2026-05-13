from collections.abc import Callable

from django.contrib import admin
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect

from shadcn_admin import utils


# Literal path prefixes for `startswith` checks. `_next` is the URL
# segment under which next.dj mounts its form-action endpoints, and
# `/static/` covers asset URLs that should never bounce to the login.
_EXEMPT_PREFIXES = (utils.LOGIN_URL, "/admin/_next/", "/static/")


class AdminPermissionMiddleware:
    """Redirect non-staff requests under `/admin/` to the login page."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the wrapped get_response callable for later dispatch."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Gate admin paths; let exempt prefixes pass through unchanged."""
        path = request.path
        if (
            path.startswith(utils.ADMIN_PREFIX)
            and not any(path.startswith(p) for p in _EXEMPT_PREFIXES)
            and not admin.site.has_permission(request)
        ):
            return HttpResponseRedirect(f"{utils.login_url()}?next={path}")
        return self.get_response(request)
