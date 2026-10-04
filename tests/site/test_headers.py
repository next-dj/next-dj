from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory, override_settings

from next.site.headers import (
    CLOSED_ROBOTS,
    ROBOTS_HEADER,
    site_robots,
    stamp_site_robots,
)
from tests.support import CLOSED_SITE


@site_robots
def _view(request: HttpRequest, word: str) -> HttpResponse:
    return HttpResponse(word)


class TestStampSiteRobots:
    """A closed site overwrites the header, an open one leaves it to the page."""

    def test_a_closed_site_overwrites(self) -> None:
        response = HttpResponse()
        response[ROBOTS_HEADER] = "all"
        with override_settings(NEXT_FRAMEWORK=CLOSED_SITE):
            stamped = stamp_site_robots(response, RequestFactory().get("/"))
        assert stamped is response
        assert response[ROBOTS_HEADER] == CLOSED_ROBOTS == "noindex, nofollow"

    def test_an_open_site_adds_nothing(self) -> None:
        response = stamp_site_robots(HttpResponse(), RequestFactory().get("/"))
        assert ROBOTS_HEADER not in response


class TestSiteRobotsDecorator:
    """The decorator stamps what a request-first view answers."""

    def test_the_view_runs_with_its_arguments(self) -> None:
        with override_settings(NEXT_FRAMEWORK=CLOSED_SITE):
            response = _view(RequestFactory().get("/"), "hi")
        assert response.content == b"hi"
        assert response[ROBOTS_HEADER] == CLOSED_ROBOTS
        assert _view.__name__ == "_view"
