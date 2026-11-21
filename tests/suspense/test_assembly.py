import asyncio
import threading
from collections.abc import Sequence
from contextvars import copy_context
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest
from django.conf import settings
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.core.exceptions import SuspiciousOperation
from django.db import transaction
from django.http import Http404, HttpRequest
from django.http.response import HttpResponseBase
from django.template import Context
from django.test import RequestFactory, override_settings
from django.utils.functional import SimpleLazyObject

from next.boundaries import boundary_failed, render_boundaries
from next.defer import (
    DEFERRALS_KEY,
    Addressing,
    BoundaryError,
    BoundaryOptions,
    DeferMode,
    DeferredCall,
    Delivery,
    ErrorKind,
    Site,
)
from next.deps import Loader
from next.deps.runner import batch_scope
from next.deps.scope import RequestScope
from next.pages.responses import mark_shared_render
from next.partial.envelope import Envelope
from next.partial.headers import STREAM_ACCEPT
from next.partial.stream import streamable
from next.suspense import assembly
from next.suspense.assembly import (
    InlineDeferrals,
    PageDeferrals,
    assembled,
    inline_sink,
    page_sink,
)
from next.suspense.components import current_deferring, deferring
from next.suspense.connections import forcing_pool
from next.suspense.coordinator import Coordinator, Settlement
from next.suspense.errors import BoundaryShedError
from next.suspense.executors import background_loop
from next.suspense.markers import Caps, Effects, Job, Outcome, Rendered, Timing
from next.suspense.partial import PartialStream
from next.suspense.policy import Delivered, StreamSlots, open_streams
from next.suspense.render import Delta, Frame, _frame
from next.suspense.response import shell
from next.suspense.session import BoundarySession
from next.suspense.signals import (
    boundary_rendered,
    boundary_side_effect,
    page_stream_declined,
)
from next.testing import capture_signals, override_next_settings


_FAILED = "render failed"
_SITE = Site(Addressing.COMPONENT, "card", DeferMode.STREAM, "page.html")
_SESSIONS = "django.contrib.sessions.middleware.SessionMiddleware"
_AUTH = "django.contrib.auth.middleware.AuthenticationMiddleware"


def _job(session: BoundarySession[Delta], html: str = "R") -> Job[Delta]:
    return Job(
        id=session.next_id(),
        key="k",
        site=_SITE,
        options=BoundaryOptions(),
        deadline=session.deadline,
        fallback=lambda kind: Rendered(
            f"F:{kind}",
            Delta(),
            kind=kind,
            failure=BoundaryError(kind, "d9", kind.status, retry=False),
        ),
        run=lambda: Rendered(html, Delta()),
    )


def _queued(html: str = "R") -> tuple[str, Job[Delta]]:
    """Queue one job into the open block and return its loading wrapper."""
    held = current_deferring()
    assert held is not None
    session = held.open()
    job = _job(session, html)
    session.queue(job)
    return shell(job.id, "div", "k", None, "L"), job


def _call() -> DeferredCall:
    renderer = Mock()
    renderer.loading.return_value = ("L", None)
    return DeferredCall(_SITE, BoundaryOptions(), "k", "div", None, renderer)


class TestAssembled:
    """A render settles every job it queued, unless it shows the shell alone."""

    def test_a_queued_job_is_run_and_spliced(self) -> None:
        html = assembled(lambda: f"[{_queued()[0]}]", collector=None, request=None)
        assert html.startswith("[<div id=")
        assert html.endswith('data-next-state="ready">R</div>]')

    def test_an_open_block_takes_the_render_and_seeds_its_sink(self) -> None:
        sink = Mock()
        context: dict[str, object] = {}
        with deferring(sink) as held:
            html = assembled(
                lambda: _queued()[0], collector=None, request=None, context=context
            )
        assert context == {DEFERRALS_KEY: sink}
        assert 'data-next-state="ready"' in html
        assert held.session is None

    def test_a_block_without_a_sink_leaves_the_context_alone(self) -> None:
        context: dict[str, object] = {}
        with deferring():
            assembled(lambda: "x", collector=None, request=None, context=context)
        assert context == {}

    def test_a_shell_delivery_runs_no_job_and_drops_its_markers(self) -> None:
        with deferring() as held:
            held.delivered = Delivered(Delivery.SHELL, "head")
            html = assembled(
                lambda: "<!--b-0-1:div-->" + _queued()[0], collector=None, request=None
            )
        assert 'data-next-state="loading"' in html
        assert html.startswith('<!--b-0-1:div--><div id="b-')
        assert html.endswith("</div>")
        assert held.session is None

    def test_a_prefetch_delivery_runs_no_job_either(self) -> None:
        with deferring() as held:
            held.delivered = Delivered(Delivery.PREFETCH, "prefetch")
            html = assembled(lambda: _queued()[0], collector=None, request=None)
        assert 'data-next-state="loading"' in html
        assert held.session is None

    def test_a_partial_shell_that_lost_its_jobs_asks_for_no_stream(self) -> None:
        request = RequestFactory().get("/", HTTP_ACCEPT=STREAM_ACCEPT)

        def render() -> str:
            _queued()
            return "lost"

        with streamable(request) as block:
            html = assembled(render, collector=None, request=request)
        assert html == "lost"
        assert block is not None
        assert (block.asked, block.deferred) == (False, None)

    def test_inside_a_job_the_render_runs_as_it_is(self) -> None:
        session = BoundarySession[Delta].open(Caps.from_settings())
        token = _frame.set(Frame(session, "b-x-0", 0))
        try:
            html = assembled(lambda: "plain", collector=None, request=None)
        finally:
            _frame.reset(token)
        assert html == "plain"

    def test_the_inline_sink_assembles_through_the_same_path(self) -> None:
        assert isinstance(inline_sink, InlineDeferrals)
        html = inline_sink.assembled(lambda: _queued()[0], collector=None, request=None)
        assert 'data-next-state="ready">R</div>' in html

    def test_a_forced_pool_places_every_job_by_the_pool_mode(self) -> None:
        with forcing_pool(), capture_signals(assembly.boundary_rendered) as recorder:
            assembled(lambda: _queued()[0] + _queued()[0], collector=None, request=None)
        reasons = {str(event.kwargs["reason"]) for event in recorder}
        assert reasons == {"pool-mode"}


def _keyed(calls: list[list[int]], keys: Sequence[int]) -> dict[int, int]:
    calls.append(sorted(keys))
    return {key: key * 10 for key in keys}


def _loads(
    loader: Loader[int, int],
    key: int,
    seen: list[RequestScope | None],
    barrier: threading.Barrier | None = None,
) -> str:
    """Queue one job that loads `key` through `loader`, noting its batch scope."""
    held = current_deferring()
    assert held is not None
    session = held.open()

    def run() -> Rendered[Delta]:
        if barrier is not None:
            barrier.wait()
        seen.append(batch_scope.get())
        return Rendered(str(loader.load_sync(key)), Delta())

    job = replace(_job(session), run=run)
    session.queue(job)
    return shell(job.id, "div", "k", None, "L")


class TestJobScope:
    """Jobs load in the scope that reaches them, else in one of their own."""

    def test_pool_jobs_share_one_batch_in_a_scope_closed_after_them(self) -> None:
        calls: list[list[int]] = []
        loader = Loader(lambda keys: _keyed(calls, keys), max_batch=3, window=3600)
        barrier = threading.Barrier(3, timeout=5)
        seen: list[RequestScope | None] = []

        def render() -> str:
            return "".join(_loads(loader, key, seen, barrier) for key in (1, 2, 3))

        with forcing_pool():
            html = assembled(render, collector=None, request=None)
        assert calls == [[1, 2, 3]]
        assert all(f">{key * 10}</div>" in html for key in (1, 2, 3))
        scope = seen[0]
        assert scope is not None
        assert seen == [scope] * 3
        assert scope._closed is True
        assert batch_scope.get() is None

    def test_a_render_reached_by_a_live_scope_borrows_it_open(self) -> None:
        loader = Loader(lambda keys: _keyed([], keys))
        scope = RequestScope()
        seen: list[RequestScope | None] = []
        context = copy_context()
        context.run(batch_scope.set, scope)
        html = context.run(
            assembled, lambda: _loads(loader, 4, seen), collector=None, request=None
        )
        assert ">40</div>" in html
        assert seen == [scope]
        assert scope._closed is False

    @pytest.mark.parametrize("borrowed", [False, True])
    def test_a_streamed_run_closes_only_a_scope_of_its_own(self, borrowed) -> None:
        loader = Loader(lambda keys: _keyed([], keys))
        seen: list[RequestScope | None] = []
        lent = RequestScope()
        context = copy_context()
        if borrowed:
            context.run(batch_scope.set, lent)
        with deferring() as held:
            _loads(loader, 5, seen)
        session = held.session
        assert session is not None
        context.run(assembly._prepare, session, None)
        source = assembly._Source(session, RequestFactory().get("/"))

        async def read() -> list[str]:
            return [
                piece.outcome.rendered.html
                async for batch in source.arun()
                for piece in batch
            ]

        assert asyncio.run(read()) == ["50"]
        [scope] = seen
        assert scope is not None
        assert (scope is lent) is borrowed
        assert scope._closed is not borrowed

    def test_a_scope_released_on_its_loop_closes_without_blocking_it(self) -> None:
        with deferring() as held:
            _loads(Loader(lambda keys: _keyed([], keys)), 6, [])
        session = held.session
        assert session is not None
        copy_context().run(assembly._prepare, session, None)
        holding = assembly._job_scope(session, None)
        holding.__enter__()
        scope = session.scope
        assert scope is not None
        scope.on_close(lambda: None)

        async def release() -> bool:
            holding.__exit__(None, None, None)
            [closing] = assembly._closing
            await asyncio.wrap_future(closing)
            return scope._closed

        loop = background_loop.get()
        assert asyncio.run_coroutine_threadsafe(release(), loop).result(5) is True
        assert assembly._closing == set()

    def test_a_scope_its_loop_never_touched_closes_without_a_round_trip(self) -> None:
        with deferring() as held:
            _queued()
        session = held.session
        assert session is not None
        copy_context().run(assembly._prepare, session, None)
        with (
            patch.object(assembly.asyncio, "run_coroutine_threadsafe") as scheduled,
            assembly._job_scope(session, None),
        ):
            scope = session.scope
        scheduled.assert_not_called()
        assert scope is not None
        assert scope._closed is True

    def test_an_inline_render_runs_its_jobs_buffered(self) -> None:
        seen: list[dict[str, object]] = []
        original = Coordinator.run

        def run(self, session, **kwargs):
            seen.append(kwargs)
            return original(self, session, **kwargs)

        with patch.object(Coordinator, "run", run):
            assembled(lambda: _queued()[0], collector=None, request=None)
        assert seen == [{"buffered": True}]


class TestStreamFacts:
    """A stream places its jobs by the transaction state they run in, after the view."""

    def _source(self) -> tuple[BoundarySession[Delta], assembly._Source]:
        request = RequestFactory().get("/")
        with deferring() as held:
            _queued()
        session = held.session
        assert session is not None
        with transaction.atomic():
            assembly._prepare(session, request, stream=True)
            source = assembly._Source(session, request)
        return session, source

    def _placed(self) -> tuple[BoundarySession[Delta], list[str]]:
        session, source = self._source()
        with capture_signals(boundary_rendered) as recorder:
            list(source.run())
        return session, [str(event.kwargs["reason"]) for event in recorder]

    @pytest.mark.django_db(transaction=True)
    def test_a_transaction_the_view_committed_pins_no_job(self) -> None:
        session, reasons = self._placed()
        assert session.facts is not None
        assert session.facts.atomic == ()
        assert reasons == ["sqlite"]

    @pytest.mark.django_db()
    def test_a_transaction_still_open_keeps_its_jobs_on_the_request_lane(self) -> None:
        session, reasons = self._placed()
        assert session.facts is not None
        assert session.facts.atomic == ("default",)
        assert reasons == ["atomic"]

    @pytest.mark.django_db(transaction=True)
    def test_the_lane_is_read_after_the_view_committed(self) -> None:
        _, source = self._source()
        assert source.lane() == "sqlite"

    @pytest.mark.django_db()
    def test_the_lane_of_a_transaction_still_open_is_atomic(self) -> None:
        _, source = self._source()
        assert source.lane() == "atomic"

    def test_a_session_without_facts_names_no_lane(self) -> None:
        session = BoundarySession[Delta].open(Caps.from_settings())
        assert assembly._Source(session, RequestFactory().get("/")).lane() is None


class TestOutcomes:
    """A settlement renders its error UI here, a lane failure announced as one."""

    def _session(self) -> BoundarySession[Delta]:
        return BoundarySession[Delta].open(Caps.from_settings())

    def test_a_settlement_renders_its_fallback(self) -> None:
        session = self._session()
        job = _job(session)
        session.queue(job)
        outcomes = assembly._Outcomes(session, None)
        outcome = outcomes.take(Settlement(job, ErrorKind.TIMEOUT))
        assert outcome.rendered.html == "F:timeout"

    def test_a_lane_failure_is_logged_and_announced(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = self._session()
        job = _job(session)
        session.queue(job)
        error = RuntimeError("lane")
        with capture_signals(boundary_failed) as recorder:
            assembly._Outcomes(session, None).take(
                Settlement(job, ErrorKind.ERROR, Timing(), error)
            )
        [event] = recorder.events_for(boundary_failed)
        assert event.kwargs["exception"] is error
        assert event.kwargs["digest"] == "d9"
        assert "raised outside its error boundary" in caplog.text

    def test_a_failure_without_an_error_ui_gets_a_fresh_digest(self) -> None:
        item = Settlement(_job(self._session()), ErrorKind.ERROR, Timing(), KeyError())
        outcome = Outcome(item.id, Rendered("", Delta(), kind=ErrorKind.ERROR))
        with capture_signals(boundary_failed) as recorder:
            assembly._raised(item, outcome, None)
        assert len(recorder.events_for(boundary_failed)[0].kwargs["digest"]) == 12

    @pytest.mark.parametrize("error", [BoundaryShedError(), KeyboardInterrupt(), None])
    def test_a_refusal_or_an_interrupt_is_no_failure(
        self, error: BaseException | None
    ) -> None:
        item = Settlement(_job(self._session()), ErrorKind.ERROR, Timing(), error)
        outcome = Outcome(item.id, Rendered("", Delta(), kind=ErrorKind.ERROR))
        with capture_signals(boundary_failed) as recorder:
            assembly._raised(item, outcome, None)
        assert len(recorder) == 0

    def test_nobody_listening_sends_nothing(self) -> None:
        item = Settlement(_job(self._session()), ErrorKind.ERROR, Timing(), KeyError())
        outcome = Outcome(item.id, Rendered("", Delta(), kind=ErrorKind.ERROR))
        with (
            patch.object(boundary_failed, "has_listeners", return_value=False),
            patch.object(boundary_failed, "send_robust") as send,
        ):
            assembly._raised(item, outcome, None)
        send.assert_not_called()


class TestSettledRequest:
    """Request state settles only for pool jobs, a shared page keeping its cache."""

    def _session(self, *, parallel: bool | None = None) -> BoundarySession[Delta]:
        session = BoundarySession[Delta].open(Caps.from_settings())
        job = _job(session)
        if parallel is not None:
            job = Job(
                id=job.id,
                key=job.key,
                site=job.site,
                options=BoundaryOptions(parallel=parallel),
                deadline=job.deadline,
                fallback=job.fallback,
                run=job.run,
            )
        session.queue(job)
        return session

    @override_settings(MIDDLEWARE=[_SESSIONS])
    def test_request_lane_jobs_settle_nothing(self) -> None:
        request = RequestFactory().get("/")
        request.session = SessionStore()
        assembly._prepare(self._session(parallel=False), request)
        assert request.session.accessed is False

    @override_settings(MIDDLEWARE=[_SESSIONS])
    def test_pool_jobs_of_a_private_page_settle_the_session(self) -> None:
        request = RequestFactory().get("/")
        request.session = SessionStore()
        with forcing_pool():
            assembly._prepare(self._session(), request)
        assert request.session.accessed is True

    @override_settings(MIDDLEWARE=[_SESSIONS, _AUTH])
    def test_pool_jobs_of_a_shared_page_load_it_unmarked(self) -> None:
        request = RequestFactory().get("/")
        request.session = SessionStore()
        mark_shared_render(request)
        with forcing_pool():
            assembly._prepare(self._session(), request)
        assert request.session.accessed is False
        assert request.user.is_authenticated is False
        assert request.session.accessed is True

    def test_a_render_without_a_request_settles_nothing(self) -> None:
        session = self._session()
        with forcing_pool():
            assembly._prepare(session, None)
        assert session.facts is not None
        assert session.snapshot is not None


class TestPageDeferrals:
    """The page sink asks the delivery policy once, at its first queued call."""

    def test_the_first_call_decides_the_delivery(self) -> None:
        request = RequestFactory().head("/")
        with deferring(page_sink, request) as held:
            page_sink.queue(Context(), _call())
            page_sink.queue(Context(), _call())
        assert held.delivered == Delivered(Delivery.SHELL, "head")

    def test_a_decided_delivery_stands(self) -> None:
        decided = Delivered(Delivery.INLINE, "method")
        with deferring(page_sink, RequestFactory().head("/")) as held:
            held.delivered = decided
            page_sink.queue(Context(), _call())
        assert held.delivered is decided

    def test_without_a_request_no_policy_runs(self) -> None:
        with deferring(page_sink) as held:
            page_sink.queue(Context(), _call())
        assert held.delivered is None

    def test_outside_a_block_the_call_renders_in_place(self) -> None:
        call = _call()
        call.renderer.body.return_value = ("B", None)
        assert isinstance(page_sink, PageDeferrals)
        assert page_sink.queue(Context(), call).endswith(">B</div>")

    def test_inside_a_job_no_policy_runs(self) -> None:
        session = BoundarySession[Delta].open(Caps.from_settings())
        token = _frame.set(Frame(session, "b-x-0", 0))
        try:
            with deferring(page_sink, RequestFactory().get("/")) as held:
                page_sink.queue(Context(), _call())
        finally:
            _frame.reset(token)
        assert held.delivered is None


def _raising(**kwargs: object) -> None:
    raise RuntimeError(kwargs["id"])


def test_a_failing_receiver_never_fails_the_page(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = BoundarySession[Delta].open(Caps.from_settings())
    job = _job(session)
    outcome = Outcome(
        job.id, Rendered("R", Delta(), effects=Effects(session_modified=True))
    )
    boundary_rendered.connect(_raising, sender=Job)
    boundary_side_effect.connect(_raising, sender=Job)
    try:
        with caplog.at_level("ERROR", logger="django.dispatch"):
            assembly._announce(job, outcome, None)
    finally:
        boundary_rendered.disconnect(_raising, sender=Job)
        boundary_side_effect.disconnect(_raising, sender=Job)
    assert caplog.text.count("Signal.send_robust()") == 2


def test_the_side_effect_kinds_follow_the_writes() -> None:
    found = assembly.Effects(session_modified=True, session_key_changed=True)
    assert assembly._written(found) == ("session", "session-key")
    assert assembly._written(assembly.Effects(messages_used=True)) == ("messages",)
    assert assembly._written(assembly.Effects(csrf_changed=True)) == ("csrf",)


class TestStreamedOutcomes:
    """A streamed job that raised past its boundary settles as its kind instead."""

    def _taken(self, error: Exception) -> Outcome[Delta]:
        session = BoundarySession[Delta].open(Caps.from_settings())
        job = _job(session)
        session.queue(job)
        outcomes = assembly._Outcomes(session, None, streamed=True)
        raised = Outcome(job.id, Rendered("", Delta(), kind=None, intended=error))
        return outcomes.take(raised)

    def test_a_missing_object_settles_quietly_as_not_found(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("ERROR"):
            outcome = self._taken(Http404())
        assert outcome.kind is ErrorKind.NOT_FOUND
        assert outcome.rendered.html == "F:not_found"
        assert caplog.records == []

    def test_a_suspicious_request_is_logged_where_django_logs_it(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("ERROR"):
            outcome = self._taken(SuspiciousOperation("tampered"))
        assert outcome.kind is ErrorKind.BAD_REQUEST
        assert [r.name for r in caplog.records] == [
            "django.security.SuspiciousOperation"
        ]

    def test_an_unexpected_error_is_logged_and_announced(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with (
            caplog.at_level("ERROR", logger="next.suspense"),
            capture_signals(boundary_failed) as recorder,
        ):
            outcome = self._taken(ValueError("down"))
        assert outcome.kind is ErrorKind.ERROR
        assert "raised outside its error boundary" in caplog.text
        (event,) = recorder.events
        assert event.kwargs["kind"] is ErrorKind.ERROR

    def test_an_unexpected_error_nobody_listens_for_is_still_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with (
            caplog.at_level("ERROR", logger="next.suspense"),
            patch.object(boundary_failed, "has_listeners", return_value=False),
            patch.object(boundary_failed, "send_robust") as send,
        ):
            outcome = self._taken(ValueError("down"))
        send.assert_not_called()
        assert outcome.rendered.html == "F:error"
        assert "raised outside its error boundary" in caplog.text

    @pytest.mark.parametrize("where", ["thread", "loop"])
    def test_a_write_nobody_listens_for_is_still_logged(
        self, caplog: pytest.LogCaptureFixture, where: str
    ) -> None:
        session = BoundarySession[Delta].open(Caps.from_settings())
        job = _job(session)
        session.queue(job)
        outcomes = assembly._Outcomes(session, None, streamed=True)
        written = Outcome(
            job.id, Rendered("R", Delta(), effects=Effects(session_modified=True))
        )
        with (
            caplog.at_level("WARNING", logger="next.suspense"),
            patch.object(boundary_side_effect, "has_listeners", return_value=False),
            patch.object(boundary_side_effect, "send_robust") as send,
            patch.object(boundary_side_effect, "asend_robust") as asend,
        ):
            if where == "loop":
                asyncio.run(outcomes.apiece(written))
            else:
                outcomes.take(written)
        send.assert_not_called()
        asend.assert_not_called()
        assert "wrote session request state" in caplog.text

    def test_the_loop_announces_an_outcome_and_hops_for_an_error_ui(self) -> None:
        session = BoundarySession[Delta].open(Caps.from_settings())
        first, second = _job(session), _job(session)
        session.queue(first)
        session.queue(second)
        outcomes = assembly._Outcomes(session, None, streamed=True)
        written = Rendered("R", Delta(), effects=Effects(session_modified=True))

        async def take() -> tuple[object, object]:
            one = await outcomes.apiece(Outcome(first.id, written))
            two = await outcomes.apiece(Settlement(second, ErrorKind.TIMEOUT))
            return one, two

        with capture_signals(boundary_rendered, boundary_side_effect) as recorder:
            one, two = asyncio.run(take())
        assert one.outcome.rendered.html == "R"
        assert two.outcome.rendered.html == "F:timeout"
        names = [event.signal for event in recorder.events]
        assert names.count(boundary_rendered) == 2
        assert names.count(boundary_side_effect) == 1


class TestPageRender:
    """A page render holds its stream slot until a stream response takes it."""

    def test_a_release_gives_the_slot_back_once(self) -> None:
        rendering = assembly.PageRender()
        slot = open_streams.acquire(100)
        assert slot is not None
        before = open_streams.open
        rendering.hold(slot)
        rendering.release()
        rendering.release()
        assert open_streams.open == before - 1

    def test_a_dropped_render_gives_its_slot_back(self) -> None:
        rendering = assembly.PageRender()
        slot = open_streams.acquire(100)
        assert slot is not None
        before = open_streams.open
        rendering.hold(slot)
        del rendering
        assert slot.held is False
        assert open_streams.open == before - 1

    def test_a_failed_render_releases_its_slot(self) -> None:
        request = RequestFactory().get("/", **{"wsgi.multithread": True})
        before = open_streams.open
        taken: list[int] = []

        def render() -> None:
            with assembly.page_deferring(request):
                page_sink.queue(Context(), _call())
                taken.append(open_streams.open)
                raise RuntimeError(_FAILED)

        with pytest.raises(RuntimeError, match=_FAILED):
            render()
        assert taken == [before + 1]
        assert open_streams.open == before

    def test_a_lost_race_for_a_slot_assembles_inline(self) -> None:
        request = RequestFactory().get("/", **{"wsgi.multithread": True})
        with (
            patch.object(StreamSlots, "acquire", return_value=None),
            assembly.page_deferring(request) as rendering,
        ):
            page_sink.queue(Context(), _call())
            held = current_deferring()
        assert held is not None
        assert held.delivered == Delivered(Delivery.INLINE, "capacity")
        assert rendering.slot is None

    def test_without_a_page_render_a_stream_takes_no_slot(self) -> None:
        request = RequestFactory().get("/", **{"wsgi.multithread": True})
        before = open_streams.open
        call = _call()
        call.renderer.body.return_value = ("B", None)
        with deferring(page_sink, request) as held:
            html = assembled(
                lambda: page_sink.queue(Context(), call),
                collector=None,
                request=request,
            )
        assert held.delivered == Delivered(Delivery.STREAM, "stream")
        assert 'data-next-state="ready"' in html
        assert open_streams.open == before

    def test_a_second_render_of_one_page_assembles_inline(self) -> None:
        request = RequestFactory().get("/", **{"wsgi.multithread": True})
        with assembly.page_deferring(request) as rendering:
            page_sink.queue(Context(), _call())
            first = assembled(lambda: _queued()[0], collector=None, request=request)
            second = assembled(lambda: _queued()[0], collector=None, request=request)
        rendering.release()
        assert 'data-next-state="loading"' in first
        assert 'data-next-state="ready"' in second

    def test_a_stream_whose_wrappers_were_dropped_assembles_inline(self) -> None:
        request = RequestFactory().get("/", **{"wsgi.multithread": True})
        with assembly.page_deferring(request) as rendering:
            page_sink.queue(Context(), _call())
            html = assembled(
                lambda: "gone" if _queued() else "", collector=None, request=request
            )
        assert html == "gone"
        assert rendering.session is None
        assert assembly.page_response(html, rendering, request).content == b"gone"
        assert rendering.slot is None


def _page_response(request: HttpRequest) -> HttpResponseBase:
    """Render a page that queues one job and return the response of its view."""
    with assembly.page_deferring(request) as rendering:
        page_sink.queue(Context(), _call())
        html = assembled(lambda: _queued()[0], collector=None, request=request)
    return assembly.page_response(html, rendering, request)


class TestPageResponse:
    """The view answers plainly without a stream and frees a slot a failure held."""

    def test_a_stream_response_takes_over_the_reservation(self) -> None:
        request = RequestFactory().get("/", **{"wsgi.multithread": True})
        before = open_streams.open
        with assembly.page_deferring(request) as rendering:
            page_sink.queue(Context(), _call())
            html = assembled(lambda: _queued()[0], collector=None, request=request)
            assert open_streams.open == before + 1
        response = assembly.page_response(html, rendering, request)
        assert isinstance(response, assembly.StreamingPageResponse)
        assert rendering.slot is None
        assert open_streams.open == before + 1
        response.close()
        assert open_streams.open == before

    def test_a_stream_response_dropped_unread_gives_its_slot_back(self) -> None:
        request = RequestFactory().get("/", **{"wsgi.multithread": True})
        before = open_streams.open
        response = _page_response(request)
        assert isinstance(response, assembly.StreamingPageResponse)
        assert open_streams.open == before + 1
        del response
        assert open_streams.open == before

    def test_a_burst_admits_no_more_streams_than_max_streams(self) -> None:
        before = open_streams.open
        decided = threading.Barrier(8)
        responses: list[HttpResponseBase] = []
        lock = threading.Lock()

        def answer() -> None:
            request = RequestFactory().get("/", **{"wsgi.multithread": True})
            response = _page_response(request)
            with lock:
                responses.append(response)
            decided.wait()

        with override_next_settings(DEFER={"MAX_STREAMS": before + 4}):
            threads = [threading.Thread(target=answer) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        streamed = [
            response
            for response in responses
            if isinstance(response, assembly.StreamingPageResponse)
        ]
        assert len(responses) == 8
        assert len(streamed) == 4
        assert open_streams.open == before + 4
        for response in responses:
            response.close()
        assert open_streams.open == before

    @pytest.mark.parametrize(
        ("csp", "minted"), [(["csp.middleware.CSPMiddleware"], ["n0"]), ([], [])]
    )
    def test_a_csp_middleware_mints_the_nonce_before_the_stream_starts(
        self, csp: list[str], minted: list[str]
    ) -> None:
        reads: list[str] = []

        def mint() -> str:
            reads.append("n0")
            return "n0"

        request = RequestFactory().get("/", **{"wsgi.multithread": True})
        request.csp_nonce = SimpleLazyObject(mint)
        middleware = [*settings.MIDDLEWARE, *csp]
        with (
            override_settings(MIDDLEWARE=middleware),
            override_next_settings(CSP_NONCE=False),
        ):
            response = _page_response(request)
        assert isinstance(response, assembly.StreamingPageResponse)
        response.close()
        assert reads == minted

    def test_a_page_without_a_render_block_is_plain(self) -> None:
        response = assembly.page_response("x", None, RequestFactory().get("/"))
        assert response.content == b"x"

    def test_a_failed_stream_frees_its_slot(self) -> None:
        rendering = assembly.PageRender()
        rendering.session = BoundarySession[Delta].open(Caps.from_settings())
        rendering.delivered = Delivered(Delivery.STREAM, "stream")
        slot = open_streams.acquire(100)
        assert slot is not None
        rendering.hold(slot)
        before = open_streams.open
        with (
            patch.object(assembly, "_stream", side_effect=RuntimeError("down")),
            pytest.raises(RuntimeError, match="down"),
        ):
            assembly.page_response("x", rendering, RequestFactory().get("/"))
        assert open_streams.open == before - 1


class TestPartialRender:
    """A streamed partial render hands its slot to the stream it answers with."""

    def _render(self) -> assembly.PartialRender:
        request = RequestFactory().get("/")
        rendering = assembly.PartialRender(
            request, Delivered(Delivery.STREAM, "stream")
        )
        slot = open_streams.acquire(100)
        assert slot is not None
        rendering.hold(slot)
        with deferring() as held:
            html = _queued()[0]
        assert held.session is not None
        rendering.shelve(held.session, html, None)
        return rendering

    def test_the_stream_owns_the_slot_until_it_closes(self) -> None:
        before = open_streams.open
        rendering = self._render()
        response = rendering.respond(Envelope("v"))
        assert isinstance(response, PartialStream)
        assert rendering.slot is None
        assert open_streams.open == before + 1
        response.close()
        assert open_streams.open == before

    def test_a_stream_that_fails_to_build_frees_the_slot(self) -> None:
        before = open_streams.open
        rendering = self._render()
        with (
            patch.object(PartialStream, "_coding", side_effect=RuntimeError(_FAILED)),
            pytest.raises(RuntimeError, match=_FAILED),
        ):
            rendering.respond(Envelope("v"))
        assert open_streams.open == before


def _partial(*, threaded: bool) -> HttpRequest:
    return RequestFactory().get(
        "/",
        headers={"X-Next-Request": "1", "Accept": STREAM_ACCEPT},
        **{"wsgi.multithread": threaded},
    )


class TestDeclined:
    """A response whose jobs are assembled inline is announced once, a stream never."""

    def _declined(self, request: HttpRequest) -> list[tuple[object, object]]:
        with (
            capture_signals(page_stream_declined) as recorder,
            assembly.page_deferring(request) as rendering,
        ):
            page_sink.queue(Context(), _call())
            page_sink.queue(Context(), _call())
        rendering.release()
        return [(event.kwargs["reason"], event.kwargs["partial"]) for event in recorder]

    def test_a_page_on_a_sync_worker_is_announced_once(self) -> None:
        assert self._declined(RequestFactory().get("/")) == [("sync-worker", False)]

    def test_a_page_past_max_streams_is_announced(self) -> None:
        request = RequestFactory().get("/", **{"wsgi.multithread": True})
        with patch.object(StreamSlots, "acquire", return_value=None):
            assert self._declined(request) == [("capacity", False)]

    def test_a_streamed_page_is_not_announced(self) -> None:
        request = RequestFactory().get("/", **{"wsgi.multithread": True})
        assert self._declined(request) == []

    def test_a_partial_kept_inline_by_the_policy_is_announced(self) -> None:
        with capture_signals(page_stream_declined) as recorder:
            assert assembly._opened(_partial(threaded=False)) is None
        [event] = recorder
        assert (event.kwargs["reason"], event.kwargs["partial"]) == (
            "sync-worker",
            True,
        )

    def test_a_partial_past_max_streams_is_announced(self) -> None:
        with (
            capture_signals(page_stream_declined) as recorder,
            patch.object(StreamSlots, "acquire", return_value=None),
        ):
            assert assembly._opened(_partial(threaded=True)) is None
        assert [event.kwargs["reason"] for event in recorder] == ["capacity"]

    def test_a_partial_head_answers_no_stream_and_no_announcement(self) -> None:
        request = RequestFactory().head(
            "/", headers={"X-Next-Request": "1"}, **{"wsgi.multithread": True}
        )
        with capture_signals(page_stream_declined) as recorder:
            assert assembly._opened(request) is None
        assert list(recorder) == []


class TestSharedBoundaryState:
    """One gathering render shares one boundary state, so its wrappers probe once."""

    def test_the_boundaries_of_a_render_share_their_state(self) -> None:
        seen: list[object] = []

        def render() -> str:
            seen.extend(render_boundaries() for _ in range(2))
            return "x"

        assembled(render, collector=None, request=None)
        with deferring():
            assembled(render, collector=None, request=None)
        assert len(seen) == 4
        assert all(scope is seen[0] for scope in seen)
        assert render_boundaries() is not seen[0]
