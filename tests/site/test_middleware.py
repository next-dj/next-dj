from django.http import Http404, HttpRequest, HttpResponse
from django.test import RequestFactory, override_settings

from next.site.middleware import RobotsHeaderMiddleware


def _live_only(request: HttpRequest | None) -> bool:
    return request is None or request.get_host() == "acme.example"


def _raising(request: HttpRequest | None) -> bool:
    raise RuntimeError


def _not_found(request: HttpRequest | None) -> bool:
    raise Http404


def _blank(request) -> HttpResponse:
    return HttpResponse()


def _answer(request) -> HttpResponse:
    response = HttpResponse("admin")
    response["X-Robots-Tag"] = "all"
    return response


class TestRobotsHeaderMiddleware:
    """The middleware closes every response of a closed site, pages or not."""

    def test_a_closed_site_overwrites_the_header(self) -> None:
        middleware = RobotsHeaderMiddleware(_answer)
        with override_settings(NEXT_FRAMEWORK={"SITE": {"INDEXABLE": False}}):
            response = middleware(RequestFactory().get("/admin/"))
        assert response["X-Robots-Tag"] == "noindex, nofollow"

    def test_an_open_site_leaves_the_response_alone(self) -> None:
        response = RobotsHeaderMiddleware(_answer)(RequestFactory().get("/admin/"))
        assert response["X-Robots-Tag"] == "all"

    def test_a_callable_closes_only_the_other_hosts(self) -> None:
        middleware = RobotsHeaderMiddleware(_blank)
        factory = RequestFactory()
        site = {"SITE": {"INDEXABLE": _live_only}}
        with override_settings(
            NEXT_FRAMEWORK=site, ALLOWED_HOSTS=["acme.example", "preview.example"]
        ):
            live = middleware(factory.get("/", HTTP_HOST="acme.example"))
            preview = middleware(factory.get("/", HTTP_HOST="preview.example"))
        assert "X-Robots-Tag" not in live
        assert preview["X-Robots-Tag"] == "noindex, nofollow"

    def test_a_raising_rule_closes_the_response(self) -> None:
        middleware = RobotsHeaderMiddleware(_answer)
        with override_settings(NEXT_FRAMEWORK={"SITE": {"INDEXABLE": _raising}}):
            response = middleware(RequestFactory().get("/admin/"))
        assert response["X-Robots-Tag"] == "noindex, nofollow"

    def test_a_404_from_the_rule_closes_the_response(self) -> None:
        middleware = RobotsHeaderMiddleware(_answer)
        with override_settings(NEXT_FRAMEWORK={"SITE": {"INDEXABLE": _not_found}}):
            response = middleware(RequestFactory().get("/admin/"))
        assert response.status_code == 200
        assert response["X-Robots-Tag"] == "noindex, nofollow"
