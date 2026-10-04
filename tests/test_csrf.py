import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from django.http import HttpResponse
from django.test import Client, RequestFactory, override_settings
from django.urls import (
    NoReverseMatch,
    clear_url_caches,
    path,
    reverse,
    set_script_prefix,
)

from next.conf import next_framework_settings
from next.csrf import (
    _REQUEST_FLAG,
    CSRF_DEFERRED_ATTR,
    CsrfDelivery,
    csrf_delivery,
    csrf_header_name,
    csrf_payload,
    csrf_token_payload,
    csrf_view,
    defer_token,
    token_deferred,
)
from next.forms.uid import ORIGIN_FIELD_NAME
from next.partial.headers import REQUEST_FLAG
from tests.support import FEED_URLCONF, isolated_form_registries, routed, write_page
from tests.support.sites import mine


RUNTIME = "<html><head></head><body>{% template %}{% collect_scripts %}</body></html>"
ACTION_PAGE = """
from django.http import HttpResponse
from next.forms import action

template = '{% form "ping" %}<button>go</button>{% endform %}'
cache = {"public": True, "s_maxage": 300}


@action("ping")
def ping():
    return HttpResponse("pong")
"""
FLAG = {"HTTP_X_NEXT_REQUEST": "1"}

# A project view named `csrf`, which is not the token endpoint.
urlpatterns = [path("account/csrf/", mine, name="csrf")]
NEVER_CACHED = "max-age=0, no-cache, no-store, must-revalidate, private"


def _action_tree(tmp_path: Path, source: str = ACTION_PAGE) -> Path:
    root = tmp_path / "pages"
    root.mkdir()
    (root / "layout.djx").write_text(RUNTIME)
    write_page(root, "", source)
    return root


def _action_and_origin(html: str) -> tuple[str, str]:
    action = re.search(r'<form action="([^"]+)"', html)
    origin = re.search(rf'name="{ORIGIN_FIELD_NAME}" value="([^"]*)"', html)
    assert action is not None
    assert origin is not None
    return action.group(1), origin.group(1)


@pytest.fixture(autouse=True)
def _form_registries():
    with isolated_form_registries():
        yield


class TestDelivery:
    """`CSRF_DELIVERY` names the mode, an unknown value reading as `auto`."""

    def test_the_default_is_auto(self) -> None:
        assert csrf_delivery() is CsrfDelivery.AUTO

    @pytest.mark.parametrize(
        ("value", "mode"),
        [("eager", CsrfDelivery.EAGER), ("lazy", CsrfDelivery.LAZY), ("x", "auto")],
    )
    def test_the_setting_is_read_once_per_reload(self, value, mode) -> None:
        with override_settings(NEXT_FRAMEWORK={"CSRF_DELIVERY": value}):
            assert csrf_delivery() == mode
            assert csrf_delivery() is csrf_delivery()
        assert csrf_delivery() is CsrfDelivery.AUTO
        assert next_framework_settings.CSRF_DELIVERY == "auto"

    def test_a_request_is_deferred_only_once_marked(self) -> None:
        request = RequestFactory().get("/")
        assert token_deferred(request) is False
        defer_token(request)
        assert token_deferred(request) is True
        assert getattr(request, CSRF_DEFERRED_ATTR) is True


class TestPayload:
    """The `$csrf` payload carries a token, or the endpoint for a deferred render."""

    def test_header_name_is_http_wire_form(self) -> None:
        with override_settings(CSRF_HEADER_NAME="HTTP_X_CSRFTOKEN"):
            assert csrf_header_name() == "X-Csrftoken"

    def test_header_name_honours_custom_setting(self) -> None:
        with override_settings(CSRF_HEADER_NAME="HTTP_X_MY_TOKEN"):
            assert csrf_header_name() == "X-My-Token"

    def test_header_name_unmangles_unprefixed_setting(self) -> None:
        with override_settings(CSRF_HEADER_NAME="X_MY_TOKEN"):
            assert csrf_header_name() == "X-My-Token"

    def test_an_eager_payload_carries_a_token(self) -> None:
        request = RequestFactory().get("/")
        payload = csrf_payload(request)
        assert set(payload) == {"header", "token"}
        assert payload["header"] == csrf_header_name()
        assert payload["token"]
        assert request.META["CSRF_COOKIE_NEEDS_UPDATE"] is True

    def test_a_deferred_payload_names_the_endpoint_and_mints_nothing(
        self, tmp_path
    ) -> None:
        request = RequestFactory().get("/")
        defer_token(request)
        with routed(_action_tree(tmp_path)):
            payload = csrf_payload(request)
        assert payload == {"header": csrf_header_name(), "url": "/_next/csrf/"}
        assert "CSRF_COOKIE_NEEDS_UPDATE" not in request.META

    def test_the_endpoint_reverses_bare_under_a_root_mount(self) -> None:
        request = RequestFactory().get("/")
        defer_token(request)
        with override_settings(ROOT_URLCONF="next.urls"):
            assert csrf_payload(request)["url"] == "/_next/csrf/"

    def test_an_unrouted_endpoint_embeds_the_token_and_logs_once(self, caplog) -> None:
        with override_settings(ROOT_URLCONF=FEED_URLCONF):
            for _ in range(2):
                request = RequestFactory().get("/")
                defer_token(request)
                payload = csrf_payload(request)
                assert set(payload) == {"header", "token"}
        [record] = [r for r in caplog.records if r.name == "next.csrf"]
        assert "include('next.urls')" in record.getMessage()

    def test_an_unrouted_endpoint_raises_under_debug(self) -> None:
        request = RequestFactory().get("/")
        defer_token(request)
        with (
            override_settings(ROOT_URLCONF=FEED_URLCONF, DEBUG=True),
            pytest.raises(NoReverseMatch) as raised,
        ):
            csrf_payload(request)
        assert "include('next.urls')" in raised.value.__notes__[0]

    def test_a_project_view_named_csrf_is_not_the_endpoint(self, caplog) -> None:
        request = RequestFactory().get("/")
        defer_token(request)
        with override_settings(ROOT_URLCONF=__name__):
            payload = csrf_payload(request)
        assert set(payload) == {"header", "token"}
        assert "include('next.urls')" in caplog.text

    def test_the_endpoint_reverses_once_per_routes_version(self, tmp_path) -> None:
        request = RequestFactory().get("/")
        defer_token(request)
        with (
            routed(_action_tree(tmp_path)),
            patch("next.csrf.reverse", side_effect=reverse) as reverses,
        ):
            first = [csrf_payload(request)["url"] for _ in range(3)]
            clear_url_caches()
            again = csrf_payload(request)["url"]
        assert first == ["/_next/csrf/"] * 3
        assert again == "/_next/csrf/"
        assert reverses.call_count == 2

    def test_the_endpoint_follows_the_script_prefix(self, tmp_path) -> None:
        request = RequestFactory().get("/")
        defer_token(request)
        with routed(_action_tree(tmp_path)):
            plain = csrf_payload(request)["url"]
            set_script_prefix("/app/")
            try:
                prefixed = csrf_payload(request)["url"]
            finally:
                set_script_prefix("/")
        assert plain == "/_next/csrf/"
        assert prefixed == "/app/_next/csrf/"

    def test_the_request_flag_is_the_partial_header(self) -> None:
        assert _REQUEST_FLAG == REQUEST_FLAG

    def test_the_token_payload_ignores_the_deferral(self) -> None:
        request = RequestFactory().get("/")
        defer_token(request)
        assert "token" in csrf_token_payload(request)


class TestEndpoint:
    """`/_next/csrf/` hands a fresh masked token to the runtime of the same site."""

    def test_a_token_answers_with_every_guarding_header(self, tmp_path) -> None:
        with routed(_action_tree(tmp_path)):
            response = Client().get("/_next/csrf/", **FLAG)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"
        assert set(json.loads(response.content)) == {"header", "token"}
        assert response["Cache-Control"] == NEVER_CACHED
        assert response["Vary"] == "Cookie"
        assert response["X-Content-Type-Options"] == "nosniff"
        assert response["Cross-Origin-Resource-Policy"] == "same-origin"
        assert response["X-Robots-Tag"] == "noindex"
        assert response.cookies["csrftoken"].value

    def test_the_mask_changes_on_every_request(self, tmp_path) -> None:
        client = Client()
        with routed(_action_tree(tmp_path)):
            first = json.loads(client.get("/_next/csrf/", **FLAG).content)
            second = json.loads(client.get("/_next/csrf/", **FLAG).content)
        assert first["token"] != second["token"]

    @pytest.mark.parametrize(
        ("method", "headers", "status"),
        [
            ("get", {}, 400),
            ("get", {"HTTP_X_NEXT_REQUEST": "true"}, 400),
            ("get", {"HTTP_X_NEXT_REQUEST": "0"}, 400),
            ("get", {**FLAG, "HTTP_SEC_FETCH_SITE": "cross-site"}, 403),
            ("get", {**FLAG, "HTTP_SEC_FETCH_SITE": "same-site"}, 403),
            ("get", {**FLAG, "HTTP_SEC_FETCH_SITE": "none"}, 403),
            ("get", {**FLAG, "HTTP_SEC_FETCH_SITE": "same-origin"}, 200),
            ("head", FLAG, 200),
            ("post", FLAG, 405),
            ("put", FLAG, 405),
            ("patch", FLAG, 405),
            ("delete", FLAG, 405),
            ("options", FLAG, 405),
        ],
    )
    def test_only_a_flagged_same_origin_read_gets_a_token(
        self, tmp_path, method, headers, status
    ) -> None:
        with routed(_action_tree(tmp_path)):
            response = getattr(Client(), method)("/_next/csrf/", **headers)
        assert response.status_code == status
        assert response["Cache-Control"] == NEVER_CACHED
        assert response["Vary"] == "Cookie"
        if status != 200:
            assert response["Content-Type"] != "application/json"
            assert "csrftoken" not in response.cookies
        if status == 405:
            assert response["Allow"] == "GET, HEAD"

    def test_a_refusal_hands_out_no_cookie(self) -> None:
        response = csrf_view(RequestFactory().get("/_next/csrf/"))
        assert response.status_code == 400
        assert not response.cookies


class TestLazyRoundTrip:
    """A shared page sets no cookie, the runtime fetches a token and posts with it."""

    def _post(self, client: Client, html: str, **headers: str) -> HttpResponse:
        action, origin = _action_and_origin(html)
        return client.post(action, {ORIGIN_FIELD_NAME: origin}, **headers)

    def test_a_shared_page_posts_after_fetching_its_token(self, tmp_path) -> None:
        client = Client(enforce_csrf_checks=True)
        with routed(_action_tree(tmp_path)):
            page = client.get("/")
            html = page.content.decode()
            refused = self._post(client, html)
            token = json.loads(client.get("/_next/csrf/", **FLAG).content)["token"]
            accepted = self._post(client, html, HTTP_X_CSRFTOKEN=token)
        assert not page.cookies
        assert page["Cache-Control"] == "public, s-maxage=300"
        assert "csrfmiddlewaretoken" not in html
        assert '"url":"/_next/csrf/"' in html
        assert refused.status_code == 403
        assert accepted.status_code == 200
        assert accepted.content == b"pong"

    def test_eager_delivery_embeds_the_token_and_goes_private(self, tmp_path) -> None:
        client = Client(enforce_csrf_checks=True)
        with routed(_action_tree(tmp_path), CSRF_DELIVERY="eager"):
            page = client.get("/")
            html = page.content.decode()
            token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
            assert token is not None
            accepted = self._post(client, html, HTTP_X_CSRFTOKEN=token.group(1))
        assert page.cookies["csrftoken"].value
        assert page["Cache-Control"] == "private"
        assert accepted.status_code == 200

    def test_lazy_delivery_defers_a_private_page_too(self, tmp_path) -> None:
        source = ACTION_PAGE.replace('cache = {"public": True, "s_maxage": 300}\n', "")
        with routed(_action_tree(tmp_path, source), CSRF_DELIVERY="lazy"):
            page = Client().get("/")
        html = page.content.decode()
        assert not page.cookies
        assert "Cache-Control" not in page
        assert "csrfmiddlewaretoken" not in html
        assert f'name="{ORIGIN_FIELD_NAME}"' in html

    @pytest.mark.django_db()
    def test_the_token_round_trip_holds_with_session_storage(self, tmp_path) -> None:
        client = Client(enforce_csrf_checks=True)
        with routed(_action_tree(tmp_path)), override_settings(CSRF_USE_SESSIONS=True):
            html = client.get("/").content.decode()
            fetched = client.get("/_next/csrf/", **FLAG)
            token = json.loads(fetched.content)["token"]
            accepted = self._post(client, html, HTTP_X_CSRFTOKEN=token)
        assert "csrftoken" not in fetched.cookies
        assert fetched.cookies["sessionid"].value
        assert accepted.status_code == 200
