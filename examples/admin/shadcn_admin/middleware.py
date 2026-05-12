"""Gate everything under `/admin/` behind `AdminSite.has_permission`.

Anonymous or non-staff users are redirected to the login page. The login
and form-action endpoints are exempt so the redirect itself can complete
and so users can POST credentials.
"""

from collections.abc import Callable

from django.contrib import admin
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect


_EXEMPT_PREFIXES = ("/admin/login/", "/admin/_next/", "/static/")


class AdminPermissionMiddleware:
    """Redirect non-staff requests under `/admin/` to the login page."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the wrapped get_response callable for later dispatch."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Gate admin paths; let exempt prefixes pass through unchanged."""
        path = request.path
        if (
            path.startswith("/admin/")
            and not any(path.startswith(p) for p in _EXEMPT_PREFIXES)
            and not admin.site.has_permission(request)
        ):
            return HttpResponseRedirect(f"/admin/login/?next={path}")
        return self.get_response(request)
