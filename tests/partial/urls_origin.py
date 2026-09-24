from pathlib import Path

from django.http import HttpRequest, HttpResponse
from django.urls import path


CAFE_PAGE = Path(__file__).resolve().parent.parent / "site_pages" / "zoned" / "page.py"


def _cafe_page(_request: HttpRequest) -> HttpResponse:
    """Stand-in page view served under a non-ASCII static route."""
    return HttpResponse("café")


_cafe_page.next_page_path = CAFE_PAGE


urlpatterns = [path("café/", _cafe_page, name="cafe_page")]
