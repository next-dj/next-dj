from django.http import HttpRequest, HttpResponse
from django.urls import path


def _items_page(_request: HttpRequest, **kwargs) -> HttpResponse:
    """Stand-in item page view carrying a string `next_page_path`."""
    return HttpResponse("item")


def _plain_view(_request: HttpRequest) -> HttpResponse:
    """Stand-in view with no page, so an invalid form answers without a re-render."""
    return HttpResponse("plain")


_items_page.next_page_path = "/items/pages/page.py"


urlpatterns = [
    path("items/<int:id>/", _items_page, name="items_page"),
    path("plain/", _plain_view, name="plain"),
]
