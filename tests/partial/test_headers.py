from unittest.mock import MagicMock, patch

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory
from django.utils.cache import patch_vary_headers

from next.partial import is_partial_request, partial_intent
from next.partial.headers import (
    ACCEPT,
    CONTENT_TYPE,
    STREAM_ACCEPT,
    VARY_HEADERS,
    MergeMode,
    PartialIntent,
    accepts_event_stream,
    set_partial_vary,
    set_stream_vary,
    vary_on,
)
from next.requests import PrefetchDepth, next_of
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
        assert intent.partial is False
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

    def test_an_empty_segment_between_names_is_dropped(self) -> None:
        # a bare comma from an empty zone slot must not look up the zone ""
        request = _request_with_headers(
            **{"X-Next-Request": "1", "X-Next-Zone": "a,,b"}
        )
        assert partial_intent(request).zones == ("a", "b")

    def test_a_trailing_comma_does_not_add_a_name(self) -> None:
        request = _request_with_headers(**{"X-Next-Request": "1", "X-Next-Zone": "a,"})
        assert partial_intent(request).zones == ("a",)

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

    def test_the_intent_lives_in_the_namespace(self) -> None:
        request = _request_with_headers(**{"X-Next-Request": "1"})
        assert partial_intent(request) is next_of(request).intent
        assert not [name for name in vars(request) if name.startswith("_next_")]

    def test_a_foreign_value_under_next_is_refused(self) -> None:
        request = _request_with_headers(**{"X-Next-Request": "1"})
        request.next = "/after-login/"
        with pytest.raises(ImproperlyConfigured, match=r"owns request\.next"):
            partial_intent(request)

    def test_parses_only_once(self) -> None:
        request = MagicMock(spec=HttpRequest)
        meta = MagicMock()
        meta.get.return_value = None
        request.META = meta
        partial_intent(request)
        partial_intent(request)
        assert meta.get.call_count == 1


class TestNonPartialIntentShared:
    """Non-partial requests share one immutable intent instead of building one."""

    def test_two_plain_requests_get_the_same_object(self) -> None:
        first = partial_intent(RequestFactory().get("/"))
        second = partial_intent(RequestFactory().get("/other/"))
        assert first is second

    def test_partial_request_gets_its_own_object(self) -> None:
        plain = partial_intent(RequestFactory().get("/"))
        partial = partial_intent(_request_with_headers(**{"X-Next-Request": "1"}))
        assert partial is not plain


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


class TestVaryOn:
    """`vary_on` leaves the `Vary` value `patch_vary_headers` leaves, computed once."""

    @pytest.mark.parametrize(
        "held", [None, "", "Cookie", "cookie, ACCEPT", "*", "Accept-Encoding,Cookie"]
    )
    @pytest.mark.parametrize(
        "names", [("Cookie",), ("Accept", "X-Next-Origin"), ("*",), VARY_HEADERS]
    )
    def test_it_merges_as_django_does(
        self, held: str | None, names: tuple[str, ...]
    ) -> None:
        fast, slow = HttpResponse(), HttpResponse()
        if held is not None:
            fast["Vary"] = slow["Vary"] = held
        vary_on(fast, names)
        patch_vary_headers(slow, names)
        assert fast["Vary"] == slow["Vary"]

    def test_a_repeated_merge_is_not_computed_again(self) -> None:
        names = ("X-Vary-On-Once",)
        vary_on(HttpResponse(), names)
        with patch("next.partial.headers.patch_vary_headers") as merged:
            response = HttpResponse()
            vary_on(response, names)
        merged.assert_not_called()
        assert response["Vary"] == "X-Vary-On-Once"

    def test_a_held_value_is_not_written_again(self) -> None:
        response = HttpResponse()
        vary_on(response, ("Cookie",))
        response.headers = MagicMock(wraps=response.headers)
        vary_on(response, ("Cookie",))
        response.headers.__setitem__.assert_not_called()


class TestZoneResponseVary:
    """A zone GET response stamps the protective partial Vary header."""

    @pytest.mark.parametrize(
        "header", ["X-Next-Request", "X-Next-Zone", "X-Next-Merge", "X-Next-Version"]
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


class TestAcceptsEventStream:
    """An explicit `text/event-stream` above zero and no lower than JSON asks for one."""

    @pytest.mark.parametrize(
        ("accept", "streams"),
        [
            pytest.param(STREAM_ACCEPT, True, id="the-client-default"),
            pytest.param("TEXT/EVENT-STREAM", True, id="any-case"),
            pytest.param("text/event-stream;charset=utf-8", True, id="other-params"),
            pytest.param(
                "application/json;q=0.5, text/event-stream;q=0.5", True, id="a-tie"
            ),
            pytest.param(
                "text/event-stream;q=0.8, application/json;q=0.2", True, id="ranked"
            ),
            pytest.param(
                "text/event-stream, text/event-stream;q=0", True, id="first-listed"
            ),
            pytest.param(
                "application/json, text/event-stream;q=0.1", False, id="json-ranked"
            ),
            pytest.param(
                "application/json, text/event-stream", True, id="equal-implicit"
            ),
            pytest.param("text/event-stream;q=bad", False, id="unreadable-q"),
            pytest.param("text/event-stream;q=2", False, id="q-above-one"),
            pytest.param("text/event-stream;q=nan", False, id="q-not-a-number"),
            pytest.param(
                "text/event-stream, application/json;q=bad", True, id="bad-json-q"
            ),
            pytest.param("text/event-stream;q=0", False, id="refused"),
            pytest.param(
                "text/event-stream; q=0.0, application/json", False, id="zero"
            ),
            pytest.param("*/*", False, id="wildcard"),
            pytest.param("text/*", False, id="type-wildcard"),
            pytest.param("application/json", False, id="json-only"),
            pytest.param("", False, id="empty"),
        ],
    )
    def test_the_ranking_decides(self, accept: str, *, streams: bool) -> None:
        request = RequestFactory().get("/", HTTP_ACCEPT=accept)
        assert accepts_event_stream(request) is streams

    def test_a_request_without_accept_reads_none(self) -> None:
        assert accepts_event_stream(RequestFactory().get("/")) is False


class TestStreamVary:
    """`set_stream_vary` adds `Accept`, keeping what the response already varies on."""

    def test_accept_joins_the_partial_headers(self) -> None:
        response = HttpResponse()
        set_partial_vary(response)
        set_stream_vary(response)
        varied = [name.strip() for name in response["Vary"].split(",")]
        assert varied == [*VARY_HEADERS, "Accept"]


class TestNavigationIntent:
    """A navigation names itself, its declared layouts and a prefetch."""

    def test_a_navigation(self) -> None:
        request = _request_with_headers(
            **{
                "X-Next-Request": "1",
                "X-Next-Navigate": "1",
                "X-Next-Layouts": "a.b",
                "X-Next-Prefetch": "1",
            }
        )
        intent = partial_intent(request)
        assert (intent.navigate, intent.layouts, intent.prefetch) == (
            True,
            "a.b",
            PrefetchDepth.SHELL,
        )

    def test_layouts_and_prefetch_need_the_navigation(self) -> None:
        request = _request_with_headers(
            **{"X-Next-Request": "1", "X-Next-Layouts": "a.b", "X-Next-Prefetch": "1"}
        )
        intent = partial_intent(request)
        assert (intent.navigate, intent.layouts, intent.prefetch) == (False, None, None)

    @pytest.mark.parametrize(
        ("value", "depth"),
        [
            ("1", PrefetchDepth.SHELL),
            ("full", PrefetchDepth.FULL),
            ("deep", None),
            (None, None),
        ],
    )
    def test_the_prefetch_depth(self, value, depth) -> None:
        headers = {"X-Next-Request": "1", "X-Next-Navigate": "1"}
        if value is not None:
            headers["X-Next-Prefetch"] = value
        intent = partial_intent(_request_with_headers(**headers))
        assert intent.prefetch is depth

    def test_a_full_prefetch_needs_the_navigation(self) -> None:
        request = _request_with_headers(
            **{"X-Next-Request": "1", "X-Next-Prefetch": "full"}
        )
        assert partial_intent(request).prefetch is None

    @pytest.mark.parametrize(
        ("value", "depth"),
        [
            ("1", PrefetchDepth.FULL),
            ("full", PrefetchDepth.FULL),
            ("deep", None),
            (None, None),
        ],
    )
    def test_a_zone_prefetch_is_always_full(self, value, depth) -> None:
        headers = {"X-Next-Request": "1", "X-Next-Zone": "sheet"}
        if value is not None:
            headers["X-Next-Prefetch"] = value
        assert partial_intent(_request_with_headers(**headers)).prefetch is depth

    def test_the_navigation_headers_are_varied(self) -> None:
        assert {"X-Next-Navigate", "X-Next-Layouts", "X-Next-Prefetch"} <= set(
            VARY_HEADERS
        )


def _sent(method: str, **headers: str) -> HttpRequest:
    meta = {"HTTP_" + name.upper().replace("-", "_"): v for name, v in headers.items()}
    return RequestFactory().generic(method, "/_next/form/x/", **meta)


class TestLayerIntent:
    """`layer` names the host page of a submission fired inside a layer."""

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_an_unsafe_request_from_a_layer(self, method: str) -> None:
        request = _sent(method, **{"X-Next-Request": "1", "X-Next-Origin": "/b/?p=2"})
        assert partial_intent(request).layer == "/b/?p=2"

    @pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "TRACE"])
    def test_a_safe_request_carrying_the_origin(self, method: str) -> None:
        request = _sent(method, **{"X-Next-Request": "1", "X-Next-Origin": "/b/"})
        intent = partial_intent(request)
        assert (intent.origin, intent.layer) == ("/b/", None)

    def test_a_submission_outside_a_layer(self) -> None:
        assert partial_intent(_sent("POST", **{"X-Next-Request": "1"})).layer is None

    def test_a_submission_without_the_runtime(self) -> None:
        request = _sent("POST", **{"X-Next-Origin": "/b/"})
        assert partial_intent(request).layer is None

    @pytest.mark.parametrize(
        "origin", ["//evil.example.com/", "https://evil.example.com/", "b/"]
    )
    def test_an_origin_off_the_site(self, origin: str) -> None:
        request = _sent("POST", **{"X-Next-Request": "1", "X-Next-Origin": origin})
        assert partial_intent(request).layer is None
