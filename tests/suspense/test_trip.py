import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.test import RequestFactory

from next.components.info import ComponentInfo
from next.components.trip import TripInput
from next.defer import (
    Addressing,
    BoundaryError,
    BoundaryOptions,
    DeferMode,
    DeferredCall,
    ErrorKind,
    Site,
)
from next.diagnostics import reset_failure_logs
from next.pages.responses import personal_render
from next.suspense import trip
from next.suspense.errors import BoundaryPropsError
from next.suspense.tokens import BoundaryTokenBackend
from next.suspense.tokens.props import DEFAULT_LIMIT
from next.testing import override_next_settings


_INFO = ComponentInfo(
    name="card",
    scope_root=Path("/nowhere"),
    scope_relative="",
    template_path=None,
    module_path=None,
    is_simple=True,
)
_FAILURE = BoundaryError(ErrorKind.ERROR, "d", None, retry=True)


class _Renderer:
    """A call renderer that names a fixed trip input."""

    def __init__(self, held: TripInput | None) -> None:
        self.held = held

    def loading(self, context):
        return "", None

    def body(self, context):
        return "", None

    def error(self, context, kind):
        return "", _FAILURE

    def trip_input(self) -> TripInput | None:
        return self.held

    def retry_input(self, failure: BoundaryError) -> TripInput | None:
        return self.held


def _call(renderer: object, mode: DeferMode = DeferMode.LOAD) -> DeferredCall:
    return DeferredCall(
        site=Site(Addressing.COMPONENT, "card", mode, "", 0),
        options=BoundaryOptions(mode=mode),
        key="k",
        tag="div",
        source=None,
        renderer=renderer,
    )


class _Foreign(BoundaryTokenBackend):
    """A backend beyond the shipped stack, which mints one fixed token."""

    def issue(self, request, claims, *, tokens=()) -> str:
        return "t"

    def redeem(self, request, token, target):
        raise NotImplementedError


def _input(**props: object) -> TripInput:
    return TripInput(_INFO, DeferMode.LOAD, props)


class TestRoute:
    def test_the_kwargs_a_view_noted_win(self) -> None:
        request = RequestFactory().get("/")
        trip.note_route(request, {"id": 3})
        assert trip.route_kwargs(request) == {"id": 3}

    def test_the_resolver_match_answers_otherwise(self) -> None:
        request = RequestFactory().get("/")
        request.resolver_match = SimpleNamespace(kwargs={"slug": "x"})
        assert trip.route_kwargs(request) == {"slug": "x"}

    def test_no_route_reads_as_no_kwargs(self) -> None:
        assert trip.route_kwargs(RequestFactory().get("/")) == {}


class TestMint:
    def test_claims_carry_the_call_and_the_url(self) -> None:
        request = RequestFactory().get("/shop/")
        trip.note_route(request, {"id": 5})
        claims = trip.claims_for(
            request,
            TripInput(_INFO, DeferMode.LOAD, {"sku": "a"}, ("x.y",)),
            DEFAULT_LIMIT,
        )
        assert claims.component == "card"
        assert claims.props == (("sku", "a"),)
        assert claims.url_path == "/shop/"
        assert claims.url_kwargs == (("id", "5"),)
        assert claims.perms == ("x.y",)

    def test_an_unbound_token_keeps_the_render_shared(self) -> None:
        request = RequestFactory().get("/")
        assert trip.mint(request, _input())
        assert not personal_render(request)

    def test_a_backend_beyond_the_stack_counts_as_bound(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = _Foreign()
        monkeypatch.setattr(trip.suspense_manager, "tokens", lambda: backend)
        request = RequestFactory().get("/")
        assert trip.mint(request, _input()) == "t"
        assert personal_render(request)

    def test_an_unencodable_prop_raises(self) -> None:
        with pytest.raises(BoundaryPropsError):
            trip.mint(RequestFactory().get("/"), _input(sku={"a": 1}))

    def test_inline_claims_cap_a_prop_at_max_bytes(self) -> None:
        with pytest.raises(BoundaryPropsError, match="over 2048 characters"):
            trip.mint(RequestFactory().get("/"), _input(sku="x" * 3000))

    def test_spilled_claims_carry_a_prop_past_max_bytes(self) -> None:
        spill = {"TOKENS": {"OPTIONS": {"STORAGE": "next.suspense.SpillStorage"}}}
        with override_next_settings(DEFER=spill):
            token = trip.mint(RequestFactory().get("/"), _input(sku="x" * 3000))
        assert len(token) <= DEFAULT_LIMIT


class TestAttributes:
    def test_the_header_transport_is_left_unnamed(self) -> None:
        assert trip.token_attributes("a.b") == ' data-next-token="a.b"'

    def test_a_query_place_is_written(self) -> None:
        with override_next_settings(
            DEFER={"TOKENS": {"OPTIONS": {"TRANSPORT": "next.suspense.QueryTransport"}}}
        ):
            text = trip.token_attributes("a")
        assert text == ' data-next-token="a" data-next-transport="query:_nb"'


class TestTripInput:
    def test_a_trip_renderer_of_a_client_mode_names_its_input(self) -> None:
        held = _input()
        assert trip.trip_input(_call(_Renderer(held))) is held

    def test_a_stream_call_names_none(self) -> None:
        assert trip.trip_input(_call(_Renderer(_input()), DeferMode.STREAM)) is None

    def test_a_plain_renderer_names_none(self) -> None:
        assert trip.trip_input(_call(object())) is None


class TestShellToken:
    def test_no_request_streams_instead(self) -> None:
        assert trip.shell_token(None, _input()) is None

    def test_a_request_gets_the_attributes(self) -> None:
        text = trip.shell_token(RequestFactory().get("/"), _input())
        assert text is not None
        assert text.startswith(' data-next-token="')

    def test_a_refusing_binding_streams_instead(self) -> None:
        with override_next_settings(
            DEFER={"TOKENS": {"OPTIONS": {"BINDING": "session"}}}
        ):
            assert trip.shell_token(RequestFactory().get("/"), _input()) is None


class TestRetryToken:
    def test_no_request_offers_none(self) -> None:
        assert trip.retry_token(None, _call(_Renderer(_input())), _FAILURE) == ""

    def test_a_plain_renderer_offers_none(self) -> None:
        assert (
            trip.retry_token(RequestFactory().get("/"), _call(object()), _FAILURE) == ""
        )

    def test_a_renderer_without_input_offers_none(self) -> None:
        call = _call(_Renderer(None))
        assert trip.retry_token(RequestFactory().get("/"), call, _FAILURE) == ""

    def test_an_input_offers_its_attributes(self) -> None:
        call = _call(_Renderer(_input()))
        text = trip.retry_token(RequestFactory().get("/"), call, _FAILURE)
        assert "data-next-token" in text

    def test_an_unencodable_prop_drops_the_retry_with_a_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        reset_failure_logs()
        call = _call(_Renderer(_input(sku={"a": 1})))
        with caplog.at_level(logging.WARNING, logger="next.suspense"):
            assert trip.retry_token(RequestFactory().get("/"), call, _FAILURE) == ""
        (record,) = caplog.records
        assert record.levelno == logging.WARNING
        assert "offers no retry token" in record.getMessage()
        assert "'sku'" in record.getMessage()
