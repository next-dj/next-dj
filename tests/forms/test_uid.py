from unittest.mock import MagicMock

import pytest
from django.http import HttpRequest
from django.test import RequestFactory

from next.forms import redirect_to_origin
from next.forms.uid import current_origin_path, validated_origin_path


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


class TestValidatedOriginPath:
    """`validated_origin_path` keeps a same-site path and refuses everything else."""

    def test_relative_path_survives(self) -> None:
        request = RequestFactory().post("/items/7/")
        assert validated_origin_path("/items/9/?q=x", request=request) == (
            "/items/9/?q=x"
        )

    def test_secure_request_keeps_a_relative_path(self) -> None:
        request = RequestFactory().post("/items/7/", secure=True)
        assert request.is_secure()
        assert validated_origin_path("/items/9/", request=request) == "/items/9/"

    @pytest.mark.parametrize(
        "target",
        [
            pytest.param("https://attacker.example.com/x/", id="offsite-https"),
            pytest.param("http://attacker.example.com/x/", id="offsite-http"),
            pytest.param("//attacker.example.com/x/", id="protocol-relative"),
            pytest.param("http://testserver/x/", id="same-host-absolute"),
            pytest.param("//testserver/x/", id="same-host-protocol-relative"),
        ],
    )
    def test_absolute_target_is_refused(self, target: str) -> None:
        """Only a path comes back, so no absolute URL survives, own host included."""
        request = RequestFactory().post("/items/7/")
        assert validated_origin_path(target, request=request) is None

    @pytest.mark.parametrize(
        "target",
        [
            pytest.param("/\\attacker.example.com/x/", id="offsite-backslash"),
            pytest.param("/\\testserver/x/", id="same-host-backslash"),
            pytest.param("/\t/attacker.example.com/x/", id="tab"),
            pytest.param("/\n/attacker.example.com/x/", id="newline"),
            pytest.param("/\r/testserver/x/", id="carriage-return-same-host"),
        ],
    )
    def test_disguised_absolute_target_is_refused(self, target: str) -> None:
        """A browser drops the backslash and the control character, so both refuse."""
        request = RequestFactory().post("/items/7/")
        assert validated_origin_path(target, request=request) is None

    def test_a_path_carrying_a_dropped_code_point_is_refused(self) -> None:
        request = RequestFactory().post("/items/7/")
        assert validated_origin_path("/items/?q=x\ty", request=request) is None

    def test_request_without_a_host_still_takes_a_relative_path(self) -> None:
        """A request built in code names no host, which no relative target needs."""
        request = HttpRequest()
        request.method = "POST"
        assert validated_origin_path("/items/9/", request=request) == "/items/9/"

    @pytest.mark.parametrize(
        "target",
        [
            pytest.param("https://testserver/x/", id="https"),
            pytest.param("//testserver/x/", id="protocol-relative"),
            pytest.param("/\\testserver/x/", id="backslash"),
        ],
    )
    def test_request_without_a_host_refuses_an_absolute_target(
        self, target: str
    ) -> None:
        request = HttpRequest()
        request.method = "POST"
        assert validated_origin_path(target, request=request) is None
