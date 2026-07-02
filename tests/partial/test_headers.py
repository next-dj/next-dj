from unittest.mock import MagicMock

import pytest
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory

from next.partial import is_partial_request, partial_intent
from next.partial.headers import (
    ACCEPT,
    CONTENT_TYPE,
    VARY_HEADERS,
    MergeMode,
    PartialIntent,
    set_partial_vary,
)
from next.testing import NextClient


def _request_with_headers(**headers: str) -> HttpRequest:
    meta = {}
    for name, value in headers.items():
        meta["HTTP_" + name.upper().replace("-", "_")] = value
    return RequestFactory().get("/", **meta)


class TestPartialIntentParsing:
    """`partial_intent` parses every request header of the wire protocol."""

    def test_absent_flag_is_not_partial(self) -> None:
        request = RequestFactory().get("/")
        intent = partial_intent(request)
        assert intent == PartialIntent()
        assert intent.is_partial is False
        assert is_partial_request(request) is False

    def test_flag_other_than_one_is_not_partial(self) -> None:
        request = _request_with_headers(**{"X-Next-Request": "0"})
        assert partial_intent(request).partial is False

    def test_flag_one_is_partial(self) -> None:
        request = _request_with_headers(**{"X-Next-Request": "1"})
        intent = partial_intent(request)
        assert intent.partial is True
        assert is_partial_request(request) is True

    def test_zones_split_on_comma(self) -> None:
        request = _request_with_headers(
            **{"X-Next-Request": "1", "X-Next-Zone": "a, b ,c"}
        )
        assert partial_intent(request).zones == ("a", "b", "c")

    def test_empty_zone_header_yields_empty(self) -> None:
        request = _request_with_headers(**{"X-Next-Request": "1", "X-Next-Zone": " , "})
        assert partial_intent(request).zones == ()

    def test_validate_fields_split(self) -> None:
        request = _request_with_headers(
            **{"X-Next-Request": "1", "X-Next-Validate": "email,name"}
        )
        assert partial_intent(request).validate_fields == ("email", "name")

    def test_merge_append(self) -> None:
        request = _request_with_headers(
            **{"X-Next-Request": "1", "X-Next-Merge": "append"}
        )
        assert partial_intent(request).merge is MergeMode.APPEND

    def test_merge_prepend(self) -> None:
        request = _request_with_headers(
            **{"X-Next-Request": "1", "X-Next-Merge": "prepend"}
        )
        assert partial_intent(request).merge is MergeMode.PREPEND

    def test_merge_unknown_is_none(self) -> None:
        request = _request_with_headers(
            **{"X-Next-Request": "1", "X-Next-Merge": "bogus"}
        )
        assert partial_intent(request).merge is None

    def test_merge_absent_is_none(self) -> None:
        request = _request_with_headers(**{"X-Next-Request": "1"})
        assert partial_intent(request).merge is None

    def test_version_request_id_origin(self) -> None:
        request = _request_with_headers(
            **{
                "X-Next-Request": "1",
                "X-Next-Version": "9f3c2e1b",
                "X-Next-Request-Id": "r1",
                "X-Next-Origin": "/kanban/board/7/",
            }
        )
        intent = partial_intent(request)
        assert intent.version == "9f3c2e1b"
        assert intent.request_id == "r1"
        assert intent.origin == "/kanban/board/7/"


class TestPartialIntentMemoised:
    """The parsed intent is memoised on the request object."""

    def test_returns_same_instance(self) -> None:
        request = _request_with_headers(**{"X-Next-Request": "1"})
        first = partial_intent(request)
        second = partial_intent(request)
        assert first is second

    def test_memoised_value_survives_header_mutation(self) -> None:
        request = _request_with_headers(**{"X-Next-Request": "1", "X-Next-Zone": "a"})
        first = partial_intent(request)
        request.META["HTTP_X_NEXT_ZONE"] = "b"
        assert partial_intent(request) is first
        assert partial_intent(request).zones == ("a",)

    def test_parses_only_once(self) -> None:
        request = MagicMock(spec=HttpRequest)
        headers = MagicMock()
        headers.get.return_value = None
        request.headers = headers
        partial_intent(request)
        partial_intent(request)
        first_call = headers.get.call_args_list
        assert len(first_call) == 1


class TestVaryHeaders:
    """`set_partial_vary` stamps the protective Vary header."""

    def test_vary_includes_merge(self) -> None:
        assert "X-Next-Merge" in VARY_HEADERS

    def test_vary_includes_request_and_zone(self) -> None:
        assert "X-Next-Request" in VARY_HEADERS
        assert "X-Next-Zone" in VARY_HEADERS

    def test_vary_includes_version(self) -> None:
        assert "X-Next-Version" in VARY_HEADERS

    def test_set_partial_vary_adds_all(self) -> None:
        response = HttpResponse()
        set_partial_vary(response)
        vary = response["Vary"]
        assert "X-Next-Request" in vary
        assert "X-Next-Zone" in vary
        assert "X-Next-Merge" in vary
        assert "X-Next-Version" in vary

    def test_set_partial_vary_preserves_existing(self) -> None:
        response = HttpResponse()
        response["Vary"] = "Cookie"
        set_partial_vary(response)
        assert "Cookie" in response["Vary"]
        assert "X-Next-Merge" in response["Vary"]


class TestZoneResponseVary:
    """A zone GET response stamps the protective partial Vary header."""

    @pytest.mark.parametrize(
        "header",
        ["X-Next-Request", "X-Next-Zone", "X-Next-Merge", "X-Next-Version"],
    )
    def test_zone_response_varies_on_partial_header(self, header: str) -> None:
        response = NextClient().get_zones("/zoned/", "alpha")
        assert header in response.get("Vary", "")


class TestProtocolConstants:
    """The vendor MIME and Accept header match the wire protocol."""

    def test_content_type(self) -> None:
        assert CONTENT_TYPE == "application/vnd.next.patches+json"

    def test_accept_lists_vendor_mime_first(self) -> None:
        assert ACCEPT.startswith("application/vnd.next.patches+json")
