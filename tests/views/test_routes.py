import pytest

from next.pages.responses import Depth, Purpose, depth_of, purpose_of
from next.testing import override_next_settings
from next.views import RouteKind
from next.views.routes import boundary_token, decide_route, note_purpose
from tests.support import build_page_request, build_zone_request


_PARTIAL = {"HTTP_X_NEXT_REQUEST": "1"}


class TestRouteKind:
    """The kinds of response a page URL answers with."""

    def test_values_are_the_lowercase_names(self) -> None:
        assert all(member == member.name.lower() for member in RouteKind)


class TestDecideRoute:
    """A token with the partial switch is `BOUNDARY`, zones `ZONE`, the rest `PAGE`."""

    def test_a_zone_request_takes_the_zone_route(self) -> None:
        assert decide_route(build_zone_request("z")) is RouteKind.ZONE

    def test_a_plain_request_takes_the_page_route(self) -> None:
        assert decide_route(build_page_request()) is RouteKind.PAGE

    def test_a_token_header_with_the_switch_takes_the_boundary_route(self, rf) -> None:
        request = rf.get(
            "/",
            HTTP_X_NEXT_BOUNDARY="1.1.abc",
            HTTP_X_NEXT_ZONE="b-0a0b0c0d-0",
            **_PARTIAL,
        )
        assert decide_route(request) is RouteKind.BOUNDARY
        assert boundary_token(request) == "1.1.abc"

    def test_a_token_without_the_switch_is_a_page(self, rf) -> None:
        request = rf.get("/", HTTP_X_NEXT_BOUNDARY="1.1.abc")
        assert decide_route(request) is RouteKind.PAGE
        assert boundary_token(request) is None

    def test_the_query_field_is_stripped_whatever_the_route(self, rf) -> None:
        request = rf.get("/?_nb=1.1.abc&q=x&q=y")
        assert decide_route(request) is RouteKind.PAGE
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
        ("transport", "sent", "kept"),
        [
            pytest.param(
                "next.suspense.BodyTransport",
                {"_nb": "t", "z": "2"},
                {"z": ["2"]},
                id="body",
            ),
            pytest.param(
                "next.suspense.BodyTransport", {"z": "2"}, {"z": ["2"]}, id="body-none"
            ),
            pytest.param(
                "next.suspense.HeaderTransport",
                {"_nb": "t", "z": "2"},
                {"_nb": ["t"], "z": ["2"]},
                id="header",
            ),
        ],
    )
    def test_a_body_transport_form_loses_the_field(
        self, transport: str, sent: dict[str, str], kept: dict[str, list[str]], rf
    ) -> None:
        with override_next_settings(
            DEFER={"TOKENS": {"OPTIONS": {"TRANSPORT": transport}}}
        ):
            request = rf.post("/", sent)
            assert decide_route(request) is RouteKind.PAGE
        assert dict(request.POST.lists()) == kept

    def test_a_query_without_the_field_is_kept_as_is(self, rf) -> None:
        request = rf.get("/?q=x")
        query = request.GET
        decide_route(request)
        assert request.GET is query

    @pytest.mark.parametrize(
        ("transport", "build"),
        [
            pytest.param(
                "next.suspense.QueryTransport",
                lambda rf: rf.get("/?_nb=1.1.abc", **_PARTIAL),
                id="query",
            ),
            pytest.param(
                "next.suspense.BodyTransport",
                lambda rf: rf.post("/", {"_nb": "1.1.abc"}, **_PARTIAL),
                id="body",
            ),
        ],
    )
    def test_a_field_transport_reads_the_token_before_the_strip(
        self, rf, transport: str, build
    ) -> None:
        with override_next_settings(
            DEFER={"TOKENS": {"OPTIONS": {"TRANSPORT": transport}}}
        ):
            request = build(rf)
            assert decide_route(request) is RouteKind.BOUNDARY
            assert boundary_token(request) == "1.1.abc"
            assert "_nb" not in request.GET

    def test_the_token_is_read_once(self, rf) -> None:
        request = rf.get("/", HTTP_X_NEXT_BOUNDARY="t", **_PARTIAL)
        assert boundary_token(request) == "t"
        request.META["HTTP_X_NEXT_BOUNDARY"] = "other"
        assert boundary_token(request) == "t"


class TestNotePurpose:
    """A navigation is a prefetch at a depth or a plain navigation, the rest no depth."""

    @pytest.mark.parametrize(
        ("value", "purpose", "depth"),
        [
            ("1", Purpose.PREFETCH, Depth.SHELL),
            ("full", Purpose.PREFETCH, Depth.FULL),
            ("deep", Purpose.NAVIGATION, None),
            (None, Purpose.NAVIGATION, None),
        ],
    )
    def test_a_navigation(self, value, purpose, depth, rf) -> None:
        headers = {"X-Next-Request": "1", "X-Next-Navigate": "1"}
        if value is not None:
            headers["X-Next-Prefetch"] = value
        request = rf.get("/", headers=headers)
        note_purpose(request, RouteKind.NAVIGATE)
        assert (purpose_of(request), depth_of(request)) == (purpose, depth)

    @pytest.mark.parametrize(
        ("headers", "purpose"),
        [({}, Purpose.DOCUMENT), ({"Sec-Purpose": "prefetch"}, Purpose.SPECULATION)],
    )
    def test_a_page_has_no_depth(self, headers, purpose, rf) -> None:
        request = rf.get("/", headers=headers)
        note_purpose(request, RouteKind.PAGE)
        assert (purpose_of(request), depth_of(request)) == (purpose, None)
