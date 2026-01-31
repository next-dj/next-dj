"""Middleware to redirect based on session auth and ensure CSRF cookie for greet example."""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect


class SessionAuthMiddleware:
    """Redirect unauthenticated to login page."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Initialize middleware with get_response callable."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Process request and redirect based on session auth."""
        username = request.session.get("username")
        path = request.path.rstrip("/") or "/"

        if path in ("/home", "/settings") and not username:
            return HttpResponseRedirect("/login/")
        if path == "/login" and username:
            return HttpResponseRedirect("/home/")

        return self.get_response(request)
