import re
from collections.abc import Callable
from urllib.parse import quote

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect


_PROTECTED: tuple[re.Pattern[str], ...] = (
    re.compile(r"^/posts/create/?$"),
    re.compile(r"^/posts/\d+/edit/?$"),
    re.compile(r"^/account/profile/?$"),
)


class LoginRequiredForPostEditorMiddleware:
    """Redirect anonymous users away from protected paths."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the next ASGI/WSGI callable."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Run the middleware for one request."""
        path = request.path
        if any(p.match(path) for p in _PROTECTED) and not request.user.is_authenticated:
            next_q = quote(path, safe="/")
            url = f"{settings.LOGIN_URL}?next={next_q}"
            return redirect(url)
        return self.get_response(request)
