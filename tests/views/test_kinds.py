from pathlib import Path

import pytest

from next.defer.lifecycle import boundary_token
from next.lifecycle import PageCall, lifecycle
from next.pages.responses import PrefetchDepth, Purpose, depth_of, purpose_of
from next.requests import RequestKind
from next.testing import override_next_settings
from next.views.kinds import note_purpose, route_request
from tests.support import build_page_request, build_zone_request


_PARTIAL = {"HTTP_X_NEXT_REQUEST": "1"}


class TestRequestKind:
    """The kinds of response a page URL answers with."""

    def test_values_are_the_lowercase_names(self) -> None:
        assert all(member == member.name.lower() for member in RequestKind)


def decide_route(request) -> RequestKind:
    """Route `request` and run the gates of its kind, as the page view does first."""
    kind = route_request(request)
    call = PageCall(request, Path("page.py"), {}, kind, {})
    if kind is RequestKind.PAGE:
        for admit in lifecycle.gates(kind).admit:
            admit(call)
    return kind


def token_of(request) -> str | None:
    return boundary_token(request)


class TestDecideRoute:
    """A token with the partial switch is `BOUNDARY`, zones `ZONE`, the rest `PAGE`."""

    def test_a_zone_request_takes_the_zone_route(self) -> None:
        assert decide_route(build_zone_request("z")) is RequestKind.ZONE

    def test_a_plain_request_takes_the_page_route(self) -> None:
        assert decide_route(build_page_request()) is RequestKind.PAGE

    def test_a_token_header_with_the_switch_takes_the_boundary_route(self, rf) -> None:
        request = rf.get(
            "/",
            HTTP_X_NEXT_BOUNDARY="1.1.abc",
            HTTP_X_NEXT_ZONE="b-0a0b0c0d-0",
            **_PARTIAL,
        )
        assert decide_route(request) is RequestKind.BOUNDARY
        assert token_of(request) == "1.1.abc"

    def test_a_token_without_the_switch_is_a_page(self, rf) -> None:
        request = rf.get("/", HTTP_X_NEXT_BOUNDARY="1.1.abc")
        assert decide_route(request) is RequestKind.PAGE
        assert token_of(request) is None

    def test_the_query_field_is_stripped_whatever_the_route(self, rf) -> None:
        request = rf.get("/?_nb=1.1.abc&q=x&q=y")
        assert decide_route(request) is RequestKind.PAGE
        assert request.GET.getlist("q") == ["x", "y"]
        assert "_nb" not in request.GET
        assert request.GET.urlencode() == "q=x&q=y"

    def test_the_raw_query_string_loses_the_field_too(self, rf) -> None:
        request = rf.get("/?a=1&_nb=1.1.abc&%5Fnb=x&b=%C3%A9&c")
        decide_route(request)
        assert request.META["QUERY_STRING"] == "a=1&b=%C3%A9&c"
        assert request.get_full_path() == "/?a=1&b=%C3%A9&c"
        assert request.build_absolute_uri() == "http://testserver/?a=1&b=%C3%A9&c"
        assert request.environ["QUERY_STRING"] == "a=1&b=%C3%A9&c"

    @pytest.mark.parametrize(
        ("carrier", "sent", "kept"),
        [
            pytest.param("body", {"_nb": "t", "z": "2"}, {"z": ["2"]}, id="body"),
            pytest.param("body", {"z": "2"}, {"z": ["2"]}, id="body-none"),
            pytest.param(
                "header",
                {"_nb": "t", "z": "2"},
                {"_nb": ["t"], "z": ["2"]},
                id="header",
            ),
        ],
    )
    def test_a_body_carrier_form_loses_the_field(
        self, carrier: str, sent: dict[str, str], kept: dict[str, list[str]], rf
    ) -> None:
        with override_next_settings(
            DEFER={"TOKENS": {"OPTIONS": {"CARRIER": carrier}}}
        ):
            request = rf.post("/", sent)
            assert decide_route(request) is RequestKind.PAGE
        assert dict(request.POST.lists()) == kept

    def test_a_query_without_the_field_is_kept_as_is(self, rf) -> None:
        request = rf.get("/?q=x")
        query = request.GET
        decide_route(request)
        assert request.GET is query

    @pytest.mark.parametrize(
        ("carrier", "build"),
        [
            pytest.param(
                "query", lambda rf: rf.get("/?_nb=1.1.abc", **_PARTIAL), id="query"
            ),
            pytest.param(
                "body",
                lambda rf: rf.post("/", {"_nb": "1.1.abc"}, **_PARTIAL),
                id="body",
            ),
        ],
    )
    def test_a_field_carrier_reads_the_token_before_the_strip(
        self, rf, carrier: str, build
    ) -> None:
        with override_next_settings(
            DEFER={"TOKENS": {"OPTIONS": {"CARRIER": carrier}}}
        ):
            request = build(rf)
            assert decide_route(request) is RequestKind.BOUNDARY
            assert token_of(request) == "1.1.abc"
            assert "_nb" not in request.GET

    def test_the_token_is_read_once(self, rf) -> None:
        request = rf.get("/", HTTP_X_NEXT_BOUNDARY="t", **_PARTIAL)
        assert token_of(request) == "t"
        request.META["HTTP_X_NEXT_BOUNDARY"] = "other"
        assert token_of(request) == "t"


class TestNotePurpose:
    """A navigation is a prefetch at a depth or a plain navigation, the rest no depth."""

    @pytest.mark.parametrize(
        ("value", "purpose", "depth"),
        [
            ("1", Purpose.PREFETCH, PrefetchDepth.SHELL),
            ("full", Purpose.PREFETCH, PrefetchDepth.FULL),
            ("deep", Purpose.NAVIGATE, None),
            (None, Purpose.NAVIGATE, None),
        ],
    )
    def test_a_navigation(self, value, purpose, depth, rf) -> None:
        headers = {"X-Next-Request": "1", "X-Next-Navigate": "1"}
        if value is not None:
            headers["X-Next-Prefetch"] = value
        request = rf.get("/", headers=headers)
        note_purpose(request, RequestKind.NAVIGATE)
        assert (purpose_of(request), depth_of(request)) == (purpose, depth)

    @pytest.mark.parametrize(
        ("headers", "purpose"),
        [({}, Purpose.DOCUMENT), ({"Sec-Purpose": "prefetch"}, Purpose.SPECULATION)],
    )
    def test_a_page_has_no_depth(self, headers, purpose, rf) -> None:
        request = rf.get("/", headers=headers)
        note_purpose(request, RequestKind.PAGE)
        assert (purpose_of(request), depth_of(request)) == (purpose, None)
