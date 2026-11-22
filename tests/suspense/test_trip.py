import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from next.components.info import ComponentInfo
from next.components.trip import TripInput
from next.defer import (
    BoundaryFailure,
    BoundaryOptions,
    DeferMode,
    DeferredCall,
    ErrorKind,
)
from next.diagnostics import reset_failure_logs
from next.pages.responses import personal_render
from next.suspense import trip
from next.suspense.errors import BoundaryPropsError
from next.suspense.tokens import BoundaryTokenBackend
from next.suspense.tokens.props import DEFAULT_LIMIT
from next.testing import override_next_settings
from tests.support.boundaries import make_call, make_site


_INFO = ComponentInfo(
    name="card",
    scope_root=Path("/nowhere"),
    scope_relative="",
    template_path=None,
    module_path=None,
    is_simple=True,
)
_FAILURE = BoundaryFailure(ErrorKind.ERROR, "d", None, retry=True)


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

    def retry_input(self, failure: BoundaryFailure) -> TripInput | None:
        return self.held


def _call(renderer: object, mode: DeferMode = DeferMode.LOAD) -> DeferredCall:
    return make_call(
        site=make_site(mode=mode, origin=""),
        options=BoundaryOptions(mode=mode),
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
    def test_the_kwargs_a_view_noted_win(self, rf) -> None:
        request = rf.get("/")
        trip.note_route(request, {"id": 3})
        assert trip.route_kwargs(request) == {"id": 3}

    def test_the_resolver_match_answers_otherwise(self, rf) -> None:
        request = rf.get("/")
        request.resolver_match = SimpleNamespace(kwargs={"slug": "x"})
        assert trip.route_kwargs(request) == {"slug": "x"}

    def test_no_route_reads_as_no_kwargs(self, rf) -> None:
        assert trip.route_kwargs(rf.get("/")) == {}


class TestMint:
    def test_claims_carry_the_call_and_the_url(self, rf) -> None:
        request = rf.get("/shop/")
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

    def test_an_unbound_token_keeps_the_render_shared(self, rf) -> None:
        request = rf.get("/")
        assert trip.mint(request, _input())
        assert not personal_render(request)

    def test_a_backend_beyond_the_stack_counts_as_bound(
        self, rf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = _Foreign()
        monkeypatch.setattr(trip.suspense_manager, "tokens", lambda: backend)
        request = rf.get("/")
        assert trip.mint(request, _input()) == "t"
        assert personal_render(request)

    def test_an_unencodable_prop_raises(self, rf) -> None:
        with pytest.raises(BoundaryPropsError, match="a mapping"):
            trip.mint(rf.get("/"), _input(sku={"a": 1}))

    def test_inline_claims_cap_a_prop_at_max_bytes(self, rf) -> None:
        with pytest.raises(BoundaryPropsError, match="over 2048 characters"):
            trip.mint(rf.get("/"), _input(sku="x" * 3000))

    def test_spilled_claims_carry_a_prop_past_max_bytes(self, rf) -> None:
        spill = {"TOKENS": {"OPTIONS": {"STORAGE": "next.suspense.SpillStorage"}}}
        with override_next_settings(DEFER=spill):
            token = trip.mint(rf.get("/"), _input(sku="x" * 3000))
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

    def test_a_request_gets_the_attributes(self, rf) -> None:
        text = trip.shell_token(rf.get("/"), _input())
        assert text is not None
        assert text.startswith(' data-next-token="')

    def test_a_refusing_binding_streams_instead(self, rf) -> None:
        with override_next_settings(
            DEFER={"TOKENS": {"OPTIONS": {"BINDING": "session"}}}
        ):
            assert trip.shell_token(rf.get("/"), _input()) is None


class TestRetryToken:
    @pytest.mark.parametrize(
        ("asked", "renderer"),
        [
            pytest.param(False, _Renderer(_input()), id="no_request"),
            pytest.param(True, object(), id="plain_renderer"),
            pytest.param(True, _Renderer(None), id="renderer_without_input"),
        ],
    )
    def test_nothing_to_retry_offers_none(self, rf, asked, renderer) -> None:
        request = rf.get("/") if asked else None
        assert trip.retry_token(request, _call(renderer), _FAILURE) == ""

    def test_an_input_offers_its_attributes(self, rf) -> None:
        call = _call(_Renderer(_input()))
        text = trip.retry_token(rf.get("/"), call, _FAILURE)
        assert "data-next-token" in text

    def test_an_unencodable_prop_drops_the_retry_with_a_warning(
        self, rf, caplog: pytest.LogCaptureFixture
    ) -> None:
        reset_failure_logs()
        call = _call(_Renderer(_input(sku={"a": 1})))
        with caplog.at_level(logging.WARNING, logger="next.suspense"):
            assert trip.retry_token(rf.get("/"), call, _FAILURE) == ""
        (record,) = caplog.records
        assert record.levelno == logging.WARNING
        assert "offers no retry token" in record.getMessage()
        assert "'sku'" in record.getMessage()
