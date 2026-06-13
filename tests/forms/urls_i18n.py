from django.conf.urls.i18n import i18n_patterns
from django.http import HttpRequest, HttpResponse
from django.urls import path
from django.utils.html import escape


def _localized_page(_request: HttpRequest, slug: str) -> HttpResponse:
    """Localized stand-in page view for origin-resolution tests."""
    return HttpResponse(f"localized {escape(slug)}")


_localized_page.next_page_path = "/i18n/pages/page.py"


urlpatterns = i18n_patterns(
    path("docs/<slug:slug>/", _localized_page, name="localized_page"),
)
