from unittest.mock import MagicMock

import pytest
from django.test import RequestFactory

from next.forms import redirect_to_origin
from next.forms.uid import current_origin_path


class TestRedirectToOrigin:
    @pytest.mark.parametrize(
        "origin",
        ["/admin/library/book/", "/admin/library/book/?q=wiz"],
        ids=("path", "path_with_query"),
    )
    def test_valid_same_site_path(self, mock_http_request, origin) -> None:
        post = MagicMock()
        post.get.return_value = origin
        request = mock_http_request(method="POST", POST=post)
        response = redirect_to_origin(request)
        assert response.status_code == 302
        assert response.url == origin

    @pytest.mark.parametrize(
        "origin",
        [
            "https://attacker.example.com/x/",
            "//attacker.example.com/x/",
            "/\\attacker.example.com/x/",
            "/\\/attacker.example.com/x/",
            "javascript:alert(1)",
            "ftp://x/y",
            "",
            "no-leading-slash",
            "/\t/attacker.example.com/x/",
            "/\t\\attacker.example.com/x/",
            "/\n/attacker.example.com/x/",
            "/\r/attacker.example.com/x/",
            "/items/?q=x\ty",
        ],
        ids=(
            "https",
            "protocol_relative",
            "backslash_protocol_relative",
            "backslash_slash",
            "javascript",
            "ftp",
            "empty",
            "relative",
            "tab_hidden_protocol_relative",
            "tab_hidden_backslash",
            "newline_hidden_protocol_relative",
            "carriage_return_hidden_protocol_relative",
            "tab_inside_query",
        ),
    )
    def test_rejects_open_redirect_attempts(self, mock_http_request, origin) -> None:
        post = MagicMock()
        post.get.return_value = origin
        request = mock_http_request(method="POST", POST=post)
        response = redirect_to_origin(request, fallback="/safe/")
        assert response.url == "/safe/"

    def test_missing_post_attribute_uses_fallback(self, mock_http_request) -> None:
        request = mock_http_request(method="GET")
        delattr(request, "POST") if hasattr(request, "POST") else None
        response = redirect_to_origin(request, fallback="/home/")
        assert response.url == "/home/"

    def test_non_string_origin_uses_fallback(self, mock_http_request) -> None:
        post = MagicMock()
        post.get.return_value = ["list", "value"]
        request = mock_http_request(method="POST", POST=post)
        response = redirect_to_origin(request, fallback="/x/")
        assert response.url == "/x/"


class TestCurrentOriginPath:
    """`current_origin_path` names the URL a form should return to."""

    def test_path_without_query(self) -> None:
        request = RequestFactory().get("/admin/library/book/")
        assert current_origin_path(request) == "/admin/library/book/"

    def test_query_string_rides_along(self) -> None:
        request = RequestFactory().get(
            "/admin/library/book/", {"status__exact": "draft", "p": "2"}
        )
        assert current_origin_path(request) == (
            "/admin/library/book/?status__exact=draft&p=2"
        )

    def test_query_string_keeps_its_client_encoding(self) -> None:
        request = RequestFactory().get("/search/?q=a%20b&tag=%D1%8F")
        assert current_origin_path(request) == "/search/?q=a%20b&tag=%D1%8F"

    def test_non_ascii_path_stays_decoded(self) -> None:
        request = RequestFactory().get("/notes/\u0442\u0435\u0441\u0442/")
        assert current_origin_path(request) == "/notes/\u0442\u0435\u0441\u0442/"

    def test_request_without_a_path_yields_none(self, mock_http_request) -> None:
        request = mock_http_request(method="GET", path="")
        assert current_origin_path(request) is None

    def test_stand_in_request_without_a_real_meta_carries_no_query(
        self, mock_http_request
    ) -> None:
        request = mock_http_request(method="GET", path="/items/")
        assert current_origin_path(request) == "/items/"
