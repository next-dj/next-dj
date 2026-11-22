import asyncio
import threading
from unittest.mock import patch

import pytest
from asgiref.sync import ThreadSensitiveContext, sync_to_async
from django.core.exceptions import SuspiciousOperation, SynchronousOnlyOperation
from django.dispatch import Signal
from django.http import Http404
from django.utils.asyncio import async_unsafe

from next.boundaries import boundary_failed
from next.defer import ErrorKind
from next.suspense.assembly import outcomes as assembly_outcomes
from next.suspense.assembly.outcomes import Outcomes
from next.suspense.coordinator import Settlement
from next.suspense.errors import BoundaryShedError
from next.suspense.markers import Effects, Job, Outcome, Rendered, Timing
from next.suspense.render import Delta
from next.suspense.signals import boundary_rendered, boundary_side_effect
from next.testing import capture_signals
from tests.support.boundaries import make_job, open_session


class TestOutcomes:
    """A settlement renders its error UI here, a lane failure announced as one."""

    def test_a_settlement_renders_its_fallback(self) -> None:
        session = open_session()
        job = make_job(session)
        session.queue(job)
        outcomes = Outcomes(session, None)
        outcome = outcomes.take(Settlement(job, ErrorKind.TIMEOUT))
        assert outcome.rendered.html == "F:timeout"

    def test_a_lane_failure_is_logged_and_announced(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = open_session()
        job = make_job(session)
        session.queue(job)
        error = RuntimeError("lane")
        with capture_signals(boundary_failed) as recorder:
            Outcomes(session, None).take(
                Settlement(job, ErrorKind.ERROR, Timing(), error)
            )
        [event] = recorder.events_for(boundary_failed)
        assert event.kwargs["exception"] is error
        assert event.kwargs["digest"] == "d9"
        assert "raised outside its error boundary" in caplog.text

    def test_a_failure_without_an_error_ui_gets_a_fresh_digest(self) -> None:
        session = open_session()
        job = make_job(session, fallback=lambda kind: Rendered("", Delta(), kind=kind))
        session.queue(job)
        with capture_signals(boundary_failed) as recorder:
            Outcomes(session, None).take(
                Settlement(job, ErrorKind.ERROR, Timing(), KeyError())
            )
        assert len(recorder.events_for(boundary_failed)[0].kwargs["digest"]) == 12

    @pytest.mark.parametrize("error", [BoundaryShedError(), KeyboardInterrupt(), None])
    def test_a_refusal_or_an_interrupt_is_no_failure(
        self, error: BaseException | None
    ) -> None:
        session = open_session()
        job = make_job(session)
        session.queue(job)
        with capture_signals(boundary_failed) as recorder:
            Outcomes(session, None).take(
                Settlement(job, ErrorKind.ERROR, Timing(), error)
            )
        assert len(recorder) == 0

    def test_nobody_listening_sends_nothing(self) -> None:
        session = open_session()
        job = make_job(session)
        session.queue(job)
        with (
            patch.object(boundary_failed, "has_listeners", return_value=False),
            patch.object(boundary_failed, "send_robust") as send,
        ):
            Outcomes(session, None).take(
                Settlement(job, ErrorKind.ERROR, Timing(), KeyError())
            )
        send.assert_not_called()


def _raising(**kwargs: object) -> None:
    raise RuntimeError(kwargs["id"])


def test_a_failing_receiver_never_fails_the_page(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = open_session()
    job = make_job(session)
    outcome = Outcome(
        job.id, Rendered("R", Delta(), effects=Effects(session_modified=True))
    )
    session.queue(job)
    boundary_rendered.connect(_raising, sender=Job)
    boundary_side_effect.connect(_raising, sender=Job)
    try:
        with caplog.at_level("ERROR", logger="django.dispatch"):
            Outcomes(session, None).take(outcome)
    finally:
        boundary_rendered.disconnect(_raising, sender=Job)
        boundary_side_effect.disconnect(_raising, sender=Job)
    assert caplog.text.count("Signal.send_robust()") == 2


@async_unsafe
def _orm_read() -> str:
    return "read"


class TestAnnouncedThread:
    """An async stream announces each outcome where a sync receiver may use the ORM."""

    @pytest.mark.parametrize(
        "signal", [boundary_rendered, boundary_side_effect], ids=["rendered", "effect"]
    )
    def test_a_receiver_runs_on_the_request_thread_of_an_async_stream(
        self, signal: Signal
    ) -> None:
        session = open_session()
        job = make_job(session)
        session.queue(job)
        outcomes = Outcomes(session, None, streamed=True)
        written = Rendered("R", Delta(), effects=Effects(session_modified=True))
        seen: list[tuple[int, str]] = []

        def receiver(**kwargs: object) -> None:
            try:
                read = _orm_read()
            except SynchronousOnlyOperation:
                read = "refused"
            seen.append((threading.get_ident(), read))

        ident = sync_to_async(threading.get_ident, thread_sensitive=True)

        async def stream() -> tuple[int, int]:
            shared = await ident()
            async with ThreadSensitiveContext():
                thread = await ident()
                await outcomes.apiece(Outcome(job.id, written))
            return shared, thread

        signal.connect(receiver, sender=Job)
        try:
            shared, thread = asyncio.run(stream())
        finally:
            signal.disconnect(receiver, sender=Job)
        assert thread != shared
        assert seen == [(thread, "read")]

    def test_an_unheard_outcome_stays_on_the_loop(self) -> None:
        session = open_session()
        job = make_job(session)
        session.queue(job)
        outcomes = Outcomes(session, None, streamed=True)
        with patch.object(assembly_outcomes, "sync_to_async") as hop:
            piece = asyncio.run(
                outcomes.apiece(Outcome(job.id, Rendered("R", Delta())))
            )
        hop.assert_not_called()
        assert piece.outcome.rendered.html == "R"


@pytest.mark.parametrize(
    ("effects", "kinds"),
    [
        pytest.param(
            Effects(session_modified=True, session_key_changed=True),
            ["session", "session-key"],
            id="session",
        ),
        pytest.param(Effects(messages_used=True), ["messages"], id="messages"),
        pytest.param(Effects(csrf_changed=True), ["csrf"], id="csrf"),
    ],
)
def test_the_side_effect_kinds_follow_the_writes(
    effects: Effects, kinds: list[str]
) -> None:
    session = open_session()
    job = make_job(session)
    session.queue(job)
    outcome = Outcome(job.id, Rendered("R", Delta(), effects=effects))
    with capture_signals(boundary_side_effect) as recorder:
        Outcomes(session, None).take(outcome)
    assert [event.kwargs["kind"] for event in recorder] == kinds


class TestStreamedOutcomes:
    """A streamed job that raised past its boundary settles as its kind instead."""

    def _taken(self, error: Exception) -> Outcome[Delta]:
        session = open_session()
        job = make_job(session)
        session.queue(job)
        outcomes = Outcomes(session, None, streamed=True)
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
        session = open_session()
        job = make_job(session)
        session.queue(job)
        outcomes = Outcomes(session, None, streamed=True)
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
        session = open_session()
        first, second = make_job(session), make_job(session)
        session.queue(first)
        session.queue(second)
        outcomes = Outcomes(session, None, streamed=True)
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
