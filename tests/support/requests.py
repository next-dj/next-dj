from collections.abc import Callable
from pathlib import Path
from typing import Final

from django.contrib.auth.views import LoginView
from django.http import HttpRequest, HttpResponse
from django.middleware.csrf import get_token
from django.urls import include, path

from next.components import ComponentInfo, render_component
from next.consent import get_consent


SITE_PAGES: Final = Path(__file__).resolve().parent.parent / "site_pages"
FORMS_PAGE: Final = SITE_PAGES / "board_forms" / "page.py"
URLCONF: Final = __name__

_TEMPLATE: Final = SITE_PAGES / "template.djx"


def control_view(request: HttpRequest) -> HttpResponse:
    """Read the request surface a Django view may touch, with no next.dj code."""
    touched = (
        request.GET,
        request.POST,
        request.FILES,
        request.COOKIES,
        request.headers,
        request.content_type,
        request.accepts("text/html"),
        request.build_absolute_uri(),
        request.user.is_authenticated,
        request.session.session_key,
        get_token(request),
    )
    return HttpResponse(str(len(touched)))


def plain_view(request: HttpRequest) -> HttpResponse:
    """Answer a plain Django view that renders a component and reads consent."""
    info = ComponentInfo(
        name="plain",
        scope_root=_TEMPLATE.parent,
        scope_relative="plain",
        template_path=_TEMPLATE,
        module_path=None,
        is_simple=True,
    )
    html = render_component(info, {}, request)
    return HttpResponse(f"{html}{sorted(get_consent(request).granted)}")


def foreign_next(
    get_response: Callable[[HttpRequest], HttpResponse],
) -> Callable[[HttpRequest], HttpResponse]:
    """Return a middleware that keeps a redirect target under `request.next`."""

    def middleware(request: HttpRequest) -> HttpResponse:
        request.next = "/home"
        return get_response(request)

    return middleware


urlpatterns = [
    path("control/", control_view),
    path("plain/", plain_view),
    path("login/", LoginView.as_view()),
    path("", include("next.urls")),
]
