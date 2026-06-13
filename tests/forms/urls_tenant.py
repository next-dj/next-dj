from django.http import HttpRequest, HttpResponse
from django.urls import path
from django.utils.html import escape


def _tenant_page(_request: HttpRequest, slug: str) -> HttpResponse:
    """Stand-in tenant page view carrying a string `next_page_path`."""
    return HttpResponse(f"tenant {escape(slug)}")


_tenant_page.next_page_path = "/tenant/pages/page.py"


def _bare_view(_request: HttpRequest) -> HttpResponse:
    """Plain view without a `next_page_path` attribute."""
    return HttpResponse("bare")


urlpatterns = [
    path("tenant/<slug:slug>/", _tenant_page, name="tenant_page"),
    path("bare/", _bare_view, name="bare_view"),
]
