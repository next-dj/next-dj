import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.template import Context
from django.test import RequestFactory, override_settings

from next.defer import (
    BoundaryFailure,
    BoundaryOptions,
    DeferMode,
    DeferredCall,
    ErrorKind,
)
from next.pages.responses import personal_render
from next.seeding import COLLECTOR_KEY, REQUEST_KEY
from next.static import StaticCollector
from next.static.runtime import BUNDLE_NOTE, URGENT_NOTE
from next.suspense import components as components_module
from next.suspense.components import (
    NOSCRIPT_PARAM,
    ComponentDeferrals,
    Deferring,
    capture,
    current_deferring,
    deferring,
)
from next.testing import override_next_settings
from tests.support.boundaries import make_call, make_site, open_session


def _call(name: str = "card") -> DeferredCall:
    renderer = SimpleNamespace(
        loading=lambda _context: ("L", None),
        body=lambda _context: ("B", None),
        error=Mock(),
    )
    return make_call("div", site=make_site(name), renderer=renderer)


class TestCapture:
    """The capture copies every `forloop` dict, the parent chain included."""

    def test_the_loop_chain_is_copied(self) -> None:
        parent = {"counter": 1, "parentloop": {}}
        loop = {"counter": 2, "parentloop": parent}
        taken = capture(Context({"forloop": loop, "item": "a"}, autoescape=False))
        loop["counter"] = 3
        parent["counter"] = 4
        assert taken.data["forloop"]["counter"] == 2
        assert taken.data["forloop"]["parentloop"]["counter"] == 1
        assert taken.data["item"] == "a"
        assert taken.autoescape is False
        assert taken.template is None

    def test_a_loop_without_a_parent_is_copied_as_it_is(self) -> None:
        taken = capture(Context({"forloop": {"counter": 1}}))
        assert taken.data["forloop"] == {"counter": 1}

    def test_a_one_shot_iterator_is_read_once_for_the_shell_and_the_job(self) -> None:
        rows = iter("abc")
        context = Context({"rows": rows})
        with context.push(other=1):
            taken = capture(context)
        assert taken.data["rows"] == ("a", "b", "c")
        assert context["rows"] == ("a", "b", "c")

    def test_the_iterator_is_replaced_where_the_shell_reads_it(self) -> None:
        hidden, shown = iter("ab"), iter("cd")
        context = Context({"rows": hidden})
        context.push(rows=shown)
        taken = capture(context)
        assert taken.data["rows"] == ("c", "d")
        assert context.dicts[-1]["rows"] == ("c", "d")
        assert context.dicts[-2]["rows"] is hidden

    def test_sequences_mappings_and_files_are_left_alone(self) -> None:
        handle = io.StringIO("x")
        kept = {"rows": ["a"], "map": {"k": 1}, "text": "abc", "file": handle}
        taken = capture(Context(kept))
        assert {key: taken.data[key] for key in kept} == kept


class TestDeferring:
    """The session opens with the first call that needs it."""

    def test_no_call_opens_no_session(self) -> None:
        with deferring() as held:
            pass
        assert held.session is None

    def test_the_session_opens_once(self) -> None:
        held = Deferring()
        assert held.open() is held.open()
        assert held.session is not None

    def test_the_block_holds_its_sink_and_request(self) -> None:
        sink, request = Mock(), Mock()
        with deferring(sink, request) as held:
            assert current_deferring() is held
        assert (held.sink, held.request, held.delivered) == (sink, request, None)
        assert current_deferring() is None

    def test_an_id_comes_from_an_open_session_alone(self) -> None:
        held = Deferring()
        assert held.minted() is None
        session = held.open()
        assert held.minted() == f"b-{session.seal}-0"
        assert held.take() is session
        assert held.session is None


class TestSink:
    """The sink queues inside a block, nests inside a job, else renders in place."""

    def test_outside_a_block_the_call_renders_in_place(self) -> None:
        html = ComponentDeferrals().inline(Context(), _call())
        assert html.startswith('<div id="b-')
        assert html.endswith(
            'data-next-boundary-key="k" data-next-state="ready">B</div>'
        )

    def test_inside_a_block_the_call_queues(self) -> None:
        with deferring() as held:
            html = ComponentDeferrals().queue(Context(), _call())
        assert held.session is not None
        [job] = held.session.jobs
        assert html.startswith(f"<!--{job.id}:div-->")
        assert job.depth == 0
        assert job.parent is None

    def test_inside_a_block_an_in_place_call_takes_a_session_id(self) -> None:
        with deferring() as held:
            html = ComponentDeferrals().inline(Context(), _call())
        assert held.session is not None
        assert html.startswith(f'<div id="b-{held.session.seal}-0"')

    def test_inside_a_job_the_call_nests(self, in_job) -> None:
        session = open_session()
        frame = in_job(session)
        html = ComponentDeferrals().queue(Context(), _call())
        [job] = frame.nested
        assert (job.parent, job.depth) == ("b-x-0", 1)
        assert session.jobs == ()
        assert html.startswith(f"<!--{job.id}:div-->")

    def test_past_the_depth_outside_debug_the_call_renders_quietly(
        self, in_job
    ) -> None:
        session = open_session()
        frame = in_job(session, depth=session.caps.depth - 1)
        with override_settings(DEBUG=False):
            html = ComponentDeferrals().queue(Context(), _call("quiet"))
        assert frame.nested == []
        assert html.startswith(f'<div id="b-{session.seal}-0"')

    def test_past_the_depth_under_debug_the_skip_is_logged_once_per_site(
        self, in_job, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(components_module, "_skipped", set())
        session = open_session()
        in_job(session, depth=session.caps.depth - 1)
        with (
            override_settings(DEBUG=True),
            caplog.at_level("DEBUG", logger="next.suspense"),
        ):
            ComponentDeferrals().queue(Context(), _call("deep"))
            ComponentDeferrals().queue(Context(), _call("deep"))
        assert caplog.text.count("its loading UI is skipped") == 1


_TRIP = SimpleNamespace(mode=DeferMode.LOAD)


def _trip_call() -> DeferredCall:
    failure = BoundaryFailure(ErrorKind.TIMEOUT, "d", None, retry=False)
    renderer = SimpleNamespace(
        loading=lambda _context: ("L", None),
        body=lambda _context: ("B", None),
        error=Mock(return_value=("E", failure)),
    )
    return make_call(
        "div",
        site=make_site(mode=DeferMode.LOAD),
        options=BoundaryOptions(mode=DeferMode.LOAD),
        renderer=renderer,
    )


@pytest.fixture()
def minted(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    tokens: list[object] = []

    def token(request: object, trip: object) -> str:
        tokens.append(trip)
        return ' data-next-token="t"'

    monkeypatch.setattr(components_module, "shell_token", token)
    return tokens


@pytest.fixture()
def context(rf: RequestFactory) -> Context:
    """Return a template context carrying a plain GET request."""
    return Context({REQUEST_KEY: rf.get("/")})


class TestTripCap:
    """Client-trip wrappers count against `MAX_INSTANCES` of their response."""

    def test_a_wrapper_past_the_cap_settles_as_a_timeout_without_a_token(
        self, context, minted: list[object]
    ) -> None:
        first, second = _trip_call(), _trip_call()
        with override_next_settings(DEFER={"MAX_INSTANCES": 1}), deferring():
            idle = ComponentDeferrals().trip(context, first, _TRIP)
            capped = ComponentDeferrals().trip(context, second, _TRIP)
        assert 'data-next-token="t"' in idle
        assert "data-next-token" not in capped
        assert 'data-next-state="error"' in capped
        assert ">E</div>" in capped
        second.renderer.error.assert_called_once_with(context, ErrorKind.TIMEOUT)
        assert len(minted) == 1

    def test_queued_stream_jobs_share_the_cap(
        self, context, minted: list[object]
    ) -> None:
        with override_next_settings(DEFER={"MAX_INSTANCES": 1}), deferring():
            ComponentDeferrals().queue(context, _call())
            capped = ComponentDeferrals().trip(context, _trip_call(), _TRIP)
        assert "data-next-token" not in capped
        assert minted == []

    def test_a_stream_job_queued_after_a_wrapper_counts_it(
        self, context, minted: list[object]
    ) -> None:
        with override_next_settings(DEFER={"MAX_INSTANCES": 1}), deferring() as held:
            idle = ComponentDeferrals().trip(context, _trip_call(), _TRIP)
            ComponentDeferrals().queue(context, _call())
            session = held.session
        assert 'data-next-token="t"' in idle
        assert session is not None
        assert session.plan().units == ()
        assert len(session.plan().expired) == 1

    def test_a_wrapper_inside_a_job_counts_its_session(
        self, context, in_job, minted: list[object]
    ) -> None:
        session = open_session()
        in_job(session)
        html = ComponentDeferrals().trip(context, _trip_call(), _TRIP)
        assert 'data-next-token="t"' in html

    def test_a_wrapper_outside_a_block_is_counted_on_its_request(
        self, context, minted: list[object]
    ) -> None:
        with override_next_settings(DEFER={"MAX_INSTANCES": 1}):
            idle = ComponentDeferrals().trip(context, _trip_call(), _TRIP)
            capped = ComponentDeferrals().trip(context, _trip_call(), _TRIP)
        assert 'data-next-token="t"' in idle
        assert "data-next-token" not in capped

    def test_a_render_without_a_request_is_never_capped(
        self, minted: list[object]
    ) -> None:
        with override_next_settings(DEFER={"MAX_INSTANCES": 0}), deferring():
            html = ComponentDeferrals().trip(Context(), _trip_call(), _TRIP)
        assert 'data-next-state="error"' not in html

    def test_the_request_of_the_block_counts_when_the_context_has_none(
        self, rf, minted: list[object]
    ) -> None:
        request = rf.get("/")
        with (
            override_next_settings(DEFER={"MAX_INSTANCES": 1}),
            deferring(request=request) as held,
        ):
            ComponentDeferrals().trip(Context(), _trip_call(), _TRIP)
            capped = ComponentDeferrals().trip(Context(), _trip_call(), _TRIP)
            session = held.session
        assert session is not None
        assert "data-next-token" not in capped


class TestRetry:
    """A plain call that failed retries with the token its trip input mints."""

    def test_the_retry_attributes_come_from_the_trip(
        self, rf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        minting = Mock(return_value=' data-next-token="t"')
        monkeypatch.setattr(components_module, "retry_attributes", minting)
        request = rf.get("/")
        assert ComponentDeferrals().retry(request, _TRIP) == ' data-next-token="t"'
        minting.assert_called_once_with(request, _TRIP)


def _adopting_call(loading: str) -> DeferredCall:
    renderer = SimpleNamespace(
        loading=lambda _context: (loading, None),
        body=lambda _context: ("<p>B</p>", None),
    )
    return make_call(
        None,
        site=make_site(mode=DeferMode.VISIBLE),
        options=BoundaryOptions(mode=DeferMode.VISIBLE),
        renderer=renderer,
    )


def _refusing_call() -> DeferredCall:
    failure = BoundaryFailure(ErrorKind.ERROR, "d", None, retry=False)
    renderer = SimpleNamespace(loading=lambda _context: ("L", failure))
    return make_call(
        "div",
        site=make_site(mode=DeferMode.LOAD),
        options=BoundaryOptions(mode=DeferMode.LOAD),
        renderer=renderer,
    )


NOSCRIPT_LINK = (
    '<noscript><a href="?q=a&amp;_nojs=1" rel="nofollow" data-next-noscript>'
    "Show this content</a></noscript>"
)


class TestNoScript:
    """An idle wrapper links a reader without scripts to the page with it in place."""

    def test_a_wrapper_holds_the_link_after_its_loading_ui(
        self, rf, minted: list[object]
    ) -> None:
        context = Context({REQUEST_KEY: rf.get("/?q=a")})
        html = ComponentDeferrals().trip(context, _trip_call(), _TRIP)
        assert html.endswith(f">L{NOSCRIPT_LINK}</div>")

    def test_an_adopting_wrapper_is_followed_by_the_link(
        self, rf, minted: list[object]
    ) -> None:
        context = Context({REQUEST_KEY: rf.get("/?q=a")})
        html = ComponentDeferrals().trip(context, _adopting_call("<p>L</p>"), _TRIP)
        assert html.endswith(f">L</p>{NOSCRIPT_LINK}")

    def test_the_link_is_built_once_per_document(
        self, rf, minted: list[object]
    ) -> None:
        request = rf.get("/?q=a")
        context = Context({REQUEST_KEY: request})
        first = ComponentDeferrals().trip(context, _trip_call(), _TRIP)
        request.GET = request.GET.copy()
        request.GET["q"] = "b"
        second = ComponentDeferrals().trip(context, _trip_call(), _TRIP)
        assert NOSCRIPT_LINK in first
        assert NOSCRIPT_LINK in second

    def test_a_partial_response_carries_no_link(self, rf, minted: list[object]) -> None:
        request = rf.get("/?q=a", headers={"X-Next-Request": "1"})
        html = ComponentDeferrals().trip(
            Context({REQUEST_KEY: request}), _trip_call(), _TRIP
        )
        assert "noscript" not in html
        assert 'data-next-token="t"' in html

    def test_a_refused_loading_ui_carries_no_link(
        self, context, minted: list[object]
    ) -> None:
        html = ComponentDeferrals().trip(context, _refusing_call(), _TRIP)
        assert "noscript" not in html

    def test_the_flagged_page_renders_the_call_in_place_privately(
        self, rf, minted: list[object]
    ) -> None:
        request = rf.get(f"/?q=a&{NOSCRIPT_PARAM}=1")
        context = Context({REQUEST_KEY: request})
        html = ComponentDeferrals().trip(context, _adopting_call("<p>L</p>"), _TRIP)
        assert 'data-next-state="ready"' in html
        assert "<p" in html
        assert ">B</p>" in html
        assert "noscript" not in html
        assert minted == []
        assert personal_render(request) is True

    def test_another_flag_value_keeps_the_trip(self, rf, minted: list[object]) -> None:
        request = rf.get(f"/?{NOSCRIPT_PARAM}=yes")
        html = ComponentDeferrals().trip(
            Context({REQUEST_KEY: request}), _trip_call(), _TRIP
        )
        assert 'data-next-token="t"' in html
        assert personal_render(request) is False


DEFER_URL = "/static/next/next.defer.min.js"


@pytest.fixture()
def collected(rf: RequestFactory) -> tuple[Context, StaticCollector]:
    """Return a context and the collector it carries, over a plain GET request."""
    collector = StaticCollector(
        bundle_url=lambda name: f"/static/next/next.{name}.min.js"
    )
    context = Context({REQUEST_KEY: rf.get("/"), COLLECTOR_KEY: collector})
    return context, collector


def _failing_call(markup: str) -> DeferredCall:
    failure = BoundaryFailure(ErrorKind.ERROR, "d", None, retry=True)
    renderer = SimpleNamespace(
        loading=lambda _context: ("L", None),
        body=lambda _context: (markup, failure),
        error=Mock(return_value=(markup, failure)),
    )
    return make_call(
        "div",
        site=make_site(mode=DeferMode.LOAD),
        options=BoundaryOptions(mode=DeferMode.LOAD),
        renderer=renderer,
    )


class TestDeferBundleNeeds:
    """A wrapper the `defer` bundle drives records the need on the page collector."""

    def test_a_load_wrapper_needs_it_urgently(
        self, collected, minted: list[object]
    ) -> None:
        context, collector = collected
        with deferring():
            ComponentDeferrals().trip(context, _trip_call(), _TRIP)
        assert collector.notes(BUNDLE_NOTE) == [DEFER_URL]
        assert collector.notes(URGENT_NOTE) == [True]

    def test_a_visible_wrapper_needs_it_lazily(
        self, collected, minted: list[object]
    ) -> None:
        context, collector = collected
        trip = SimpleNamespace(mode=DeferMode.VISIBLE)
        with deferring():
            ComponentDeferrals().trip(context, _trip_call(), trip)
        assert collector.notes(BUNDLE_NOTE) == [DEFER_URL]
        assert collector.notes(URGENT_NOTE) == ()

    def test_a_capped_wrapper_offering_a_retry_needs_it(
        self, collected, minted: list[object], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(components_module, "_take_instance", lambda _request: False)
        context, collector = collected
        retrying = _failing_call("<b data-next-retry>again</b>")
        ComponentDeferrals().trip(context, retrying, _TRIP)
        assert collector.notes(BUNDLE_NOTE) == [DEFER_URL]
        assert minted == []

    def test_a_capped_wrapper_without_a_retry_needs_nothing(
        self, collected, minted: list[object], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(components_module, "_take_instance", lambda _request: False)
        context, collector = collected
        ComponentDeferrals().trip(context, _trip_call(), _TRIP)
        assert collector.notes(BUNDLE_NOTE) == ()

    def test_an_inline_failure_retried_by_token_needs_it(
        self, collected, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            components_module, "retry_token", lambda *_args: ' data-next-token="t"'
        )
        context, collector = collected
        html = ComponentDeferrals().inline(context, _failing_call("<b>x</b>"))
        assert 'data-next-token="t"' in html
        assert collector.notes(BUNDLE_NOTE) == [DEFER_URL]

    def test_an_inline_success_needs_nothing(self, collected) -> None:
        context, collector = collected
        ComponentDeferrals().inline(context, _call())
        assert collector.notes(BUNDLE_NOTE) == ()
