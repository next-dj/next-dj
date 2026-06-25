from django.http import HttpRequest, HttpResponse
from django.urls import path


def _items_page(_request: HttpRequest, **_kwargs: object) -> HttpResponse:
    """Stand-in item page view carrying a string `next_page_path`."""
    return HttpResponse("item")


_items_page.next_page_path = "/items/pages/page.py"


urlpatterns = [
    path("items/<int:id>/", _items_page, name="items_page"),
]
