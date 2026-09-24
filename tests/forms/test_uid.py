from unittest.mock import MagicMock

import pytest
from django.test import RequestFactory
from django.utils.http import MAX_URL_LENGTH

from next.forms import redirect_to_origin
from next.forms.uid import (
    MAX_ORIGIN_LENGTH,
    ORIGIN_FIELD_NAME,
    current_origin_path,
    posted_origin_path,
    redirect_or_fallback,
    validated_origin_path,
)


_LONG_QUERY_ORIGIN = "/items/?q=" + "x" * MAX_URL_LENGTH
_OVERSIZE_ORIGIN = "/search/?q=" + "\u044f" * 3000


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

    def test_origin_past_the_length_cap_is_kept(self) -> None:
        request = RequestFactory().post("/", {ORIGIN_FIELD_NAME: _LONG_QUERY_ORIGIN})
        assert redirect_to_origin(request).url == _LONG_QUERY_ORIGIN

    @pytest.mark.usefixtures("cap_redirects")
    def test_origin_past_the_redirect_cap_uses_fallback(self) -> None:
        request = RequestFactory().post("/", {ORIGIN_FIELD_NAME: _OVERSIZE_ORIGIN})
        response = redirect_to_origin(request, fallback="/safe/")
        assert response.url == "/safe/"


class TestRedirectOrFallback:
    """`redirect_or_fallback` answers the target unless Django refuses the Location."""

    def test_accepted_target_carries_the_status(self) -> None:
        response = redirect_or_fallback("/items/", "/", status=303)
        assert response.status_code == 303
        assert response.url == "/items/"

    def test_default_status_is_found(self) -> None:
        assert redirect_or_fallback("/items/", "/").status_code == 302

    @pytest.mark.usefixtures("cap_redirects")
    def test_refused_target_redirects_to_the_fallback(self) -> None:
        response = redirect_or_fallback(_OVERSIZE_ORIGIN, "/safe/", status=303)
        assert response.status_code == 303
        assert response.url == "/safe/"

    def test_disallowed_scheme_redirects_to_the_fallback(self) -> None:
        assert redirect_or_fallback("javascript:alert(1)", "/safe/").url == "/safe/"


class TestPostedOriginPath:
    """`posted_origin_path` reads the origin field of a POST and nothing else."""

    def test_post_yields_its_validated_origin(self) -> None:
        request = RequestFactory().post("/", {ORIGIN_FIELD_NAME: "/items/?q=x"})
        assert posted_origin_path(request) == "/items/?q=x"

    def test_post_with_an_over_cap_origin_yields_none(self) -> None:
        oversize = "/" + '"' * 2_500_000
        request = RequestFactory().post("/", {ORIGIN_FIELD_NAME: oversize})
        assert posted_origin_path(request) is None

    def test_post_with_an_offsite_origin_yields_none(self) -> None:
        request = RequestFactory().post("/", {ORIGIN_FIELD_NAME: "//evil.example/"})
        assert posted_origin_path(request) is None

    def test_non_post_request_yields_none(self, mock_http_request) -> None:
        post = MagicMock()
        post.get.return_value = "/items/"
        request = mock_http_request(method="GET", POST=post)
        assert posted_origin_path(request) is None
        post.get.assert_not_called()


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

    def test_non_ascii_path_is_percent_encoded(self) -> None:
        request = RequestFactory().get("/notes/\u0442\u0435\u0441\u0442/")
        assert current_origin_path(request) == "/notes/%D1%82%D0%B5%D1%81%D1%82/"

    def test_question_mark_in_a_segment_stays_in_the_path(self) -> None:
        request = RequestFactory().get("/groups/a%3Fb/", {"q": "1"})
        assert current_origin_path(request) == "/groups/a%3Fb/?q=1"

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

    @pytest.mark.parametrize(
        "target", ["/items/9/", "/items/9/?q=x"], ids=("path", "path_with_query")
    )
    def test_relative_path_survives(self, target: str) -> None:
        assert validated_origin_path(target) == target

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
        assert validated_origin_path(target) is None

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
        assert validated_origin_path(target) is None

    def test_a_path_carrying_a_dropped_code_point_is_refused(self) -> None:
        assert validated_origin_path("/items/?q=x\ty") is None

    def test_a_path_past_the_url_length_cap_survives(self) -> None:
        """A long filter query past Django's 2048 URL cap is still a same-site path."""
        assert validated_origin_path(_LONG_QUERY_ORIGIN) == _LONG_QUERY_ORIGIN

    def test_a_path_at_the_origin_cap_survives(self) -> None:
        at_cap = "/" + "a" * (MAX_ORIGIN_LENGTH - 1)
        assert validated_origin_path(at_cap) == at_cap

    def test_a_path_past_the_origin_cap_is_refused(self) -> None:
        assert validated_origin_path("/" + "a" * MAX_ORIGIN_LENGTH) is None

    def test_the_cap_reads_the_length_before_the_strip(self) -> None:
        """Padding counts, so an over-cap value is refused without stripping it."""
        padded = " " + "/" + "a" * (MAX_ORIGIN_LENGTH - 1)
        assert validated_origin_path(padded) is None

    def test_a_long_real_page_url_survives(self) -> None:
        page_url = "/catalog/" + "b" * 3000 + "/?page=2"
        assert validated_origin_path(page_url) == page_url

    def test_a_non_string_is_refused(self) -> None:
        assert validated_origin_path(None) is None
