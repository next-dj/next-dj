import asyncio
import sys
import threading
from dataclasses import replace
from unittest.mock import patch

import pytest
from asgiref.sync import ThreadSensitiveContext, sync_to_async
from django.core.exceptions import (
    PermissionDenied,
    SuspiciousOperation,
    SynchronousOnlyOperation,
)
from django.core.signals import got_request_exception
from django.dispatch import Signal
from django.http import Http404
from django.utils.asyncio import async_unsafe

from next.boundaries import boundary_failed
from next.boundaries.failures import ErrorKind
from next.defer.assembly import outcomes as assembly_outcomes
from next.defer.assembly.outcomes import Outcomes
from next.defer.errors import BoundaryDeadlineError, BoundaryShedError
from next.defer.markers import Caps, Delta, Effects, Job, Outcome, Rendered
from next.defer.signals import boundary_settled, boundary_state_written
from next.testing import capture_signals
from tests.support.boundaries import make_job, open_session


class TestOutcomes:
    """A settlement renders its error UI here, a lane failure announced as one."""

    def test_a_settlement_renders_its_error_ui(self) -> None:
        session = open_session()
        job = make_job(session)
        session.queue(job)
        outcomes = Outcomes(session, None)
        outcome = outcomes.take(Outcome(job, settled=ErrorKind.TIMEOUT))
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
                Outcome(job, settled=ErrorKind.ERROR, error=error)
            )
        [event] = recorder.events_for(boundary_failed)
        assert event.kwargs["exception"] is error
        assert event.kwargs["digest"] == "d9"
        [record] = [r for r in caplog.records if r.name == "django.request"]
        assert "raised outside its error boundary" in record.getMessage()
        assert record.exc_info is not None
        assert record.exc_info[1] is error

    def test_a_lane_failure_reaches_got_request_exception_every_time(
        self, rf, caplog: pytest.LogCaptureFixture
    ) -> None:
        request = rf.get("/")
        session = open_session()
        job = make_job(session)
        session.queue(job)
        outcomes = Outcomes(session, request)
        handled: list[BaseException | None] = []

        def receiver(**kwargs: object) -> None:
            handled.append(sys.exception())

        errors = [RuntimeError("first"), RuntimeError("second")]
        got_request_exception.connect(receiver, weak=False)
        try:
            with caplog.at_level("DEBUG", logger="django.request"):
                for error in errors:
                    outcomes.take(Outcome(job, settled=ErrorKind.ERROR, error=error))
        finally:
            got_request_exception.disconnect(receiver)
        assert handled == errors
        levels = [r.levelname for r in caplog.records if r.name == "django.request"]
        assert levels == ["ERROR", "DEBUG"]

    def test_a_failure_without_an_error_ui_gets_a_fresh_digest(self) -> None:
        session = open_session()
        job = make_job(session, error_ui=lambda kind: Rendered("", Delta(), kind=kind))
        session.queue(job)
        with capture_signals(boundary_failed) as recorder:
            Outcomes(session, None).take(
                Outcome(job, settled=ErrorKind.ERROR, error=KeyError())
            )
        assert len(recorder.events_for(boundary_failed)[0].kwargs["digest"]) == 12

    @pytest.mark.parametrize(
        ("error", "kind", "said"),
        [
            (None, ErrorKind.TIMEOUT, "outran the earlier of its timeout="),
            (BoundaryDeadlineError(), ErrorKind.TIMEOUT, "outran the earlier"),
            (None, ErrorKind.OVERLOADED, "MAX_WORKERS and MAX_QUEUE"),
            (BoundaryShedError(), ErrorKind.OVERLOADED, "MAX_WORKERS and MAX_QUEUE"),
        ],
    )
    def test_a_job_settled_without_a_result_is_warned_of_once_per_site(
        self,
        rf,
        caplog: pytest.LogCaptureFixture,
        error: BaseException | None,
        kind: ErrorKind,
        said: str,
    ) -> None:
        session = open_session()
        job = make_job(session)
        session.queue(job)
        outcomes = Outcomes(session, rf.get("/shop/"))
        with caplog.at_level("WARNING", logger="next.defer"):
            for _ in range(3):
                outcomes.take(Outcome(job, settled=kind, error=error))
        [record] = caplog.records
        message = record.getMessage()
        assert message.startswith(
            f"The deferred job of component 'card' in page.html on /shop/ settled as "
            f"{kind} without a result"
        )
        assert said in message

    def test_a_job_past_max_instances_names_the_setting(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = open_session(replace(Caps.from_settings(), instances=1))
        first, second = make_job(session), make_job(session)
        session.queue(first)
        session.queue(second)
        assert session.expired == (second,)
        with caplog.at_level("WARNING", logger="next.defer"):
            Outcomes(session, None).take(Outcome(second, settled=ErrorKind.TIMEOUT))
        [record] = caplog.records
        assert "NEXT_FRAMEWORK['DEFER']['MAX_INSTANCES'] allows" in record.getMessage()

    @pytest.mark.parametrize("error", [BoundaryShedError(), KeyboardInterrupt(), None])
    def test_a_refusal_or_an_interrupt_is_no_failure(
        self, error: BaseException | None
    ) -> None:
        session = open_session()
        job = make_job(session)
        session.queue(job)
        with capture_signals(boundary_failed) as recorder:
            Outcomes(session, None).take(
                Outcome(job, settled=ErrorKind.ERROR, error=error)
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
                Outcome(job, settled=ErrorKind.ERROR, error=KeyError())
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
        job, Rendered("R", Delta(), effects=Effects(session_modified=True))
    )
    session.queue(job)
    boundary_settled.connect(_raising, sender=Job)
    boundary_state_written.connect(_raising, sender=Job)
    try:
        with caplog.at_level("ERROR", logger="django.dispatch"):
            Outcomes(session, None).take(outcome)
    finally:
        boundary_settled.disconnect(_raising, sender=Job)
        boundary_state_written.disconnect(_raising, sender=Job)
    assert caplog.text.count("Signal.send_robust()") == 2


@async_unsafe
def _orm_read() -> str:
    return "read"


class TestAnnouncedThread:
    """An async stream announces each outcome where a sync receiver may use the ORM."""

    @pytest.mark.parametrize(
        "signal", [boundary_settled, boundary_state_written], ids=["settled", "written"]
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
                await outcomes.atake(Outcome(job, written))
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
        with patch.object(assembly_outcomes, "on_request_thread") as hop:
            piece = asyncio.run(outcomes.atake(Outcome(job, Rendered("R", Delta()))))
        hop.assert_not_called()
        assert piece.rendered.html == "R"


@pytest.mark.parametrize(
    ("effects", "written"),
    [
        pytest.param(
            Effects(session_modified=True, session_key_changed=True),
            ["session", "session_key"],
            id="session",
        ),
        pytest.param(Effects(messages_used=True), ["messages"], id="messages"),
        pytest.param(Effects(csrf_changed=True), ["csrf"], id="csrf"),
    ],
)
def test_the_written_states_follow_the_writes(
    effects: Effects, written: list[str]
) -> None:
    session = open_session()
    job = make_job(session)
    session.queue(job)
    outcome = Outcome(job, Rendered("R", Delta(), effects=effects))
    with capture_signals(boundary_state_written) as recorder:
        Outcomes(session, None).take(outcome)
    assert [event.kwargs["written"] for event in recorder] == written


class TestStreamedOutcomes:
    """A streamed job that raised past its boundary settles as its kind instead."""

    def _taken(self, error: Exception) -> Outcome:
        session = open_session()
        job = make_job(session)
        session.queue(job)
        outcomes = Outcomes(session, None, streamed=True)
        raised = Outcome(job, Rendered("", Delta(), kind=None, intended=error))
        return outcomes.take(raised)

    @pytest.mark.parametrize(
        ("error", "kind", "status"),
        [
            (Http404(), ErrorKind.NOT_FOUND, 404),
            (PermissionDenied(), ErrorKind.FORBIDDEN, 403),
        ],
    )
    def test_a_status_the_stream_cannot_carry_is_warned_of_once(
        self,
        caplog: pytest.LogCaptureFixture,
        error: Exception,
        kind: ErrorKind,
        status: int,
    ) -> None:
        with caplog.at_level("WARNING"):
            outcomes = [self._taken(error) for _ in range(2)]
        assert {outcome.kind for outcome in outcomes} == {kind}
        assert outcomes[0].rendered.html == f"F:{kind}"
        [record] = caplog.records
        assert record.levelname == "WARNING"
        assert record.status_code == status
        message = record.getMessage()
        assert f"raised {type(error).__name__} after the stream sent its 200" in message
        assert "in page.html on no request" in message

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
            caplog.at_level("ERROR", logger="next.defer"),
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
            caplog.at_level("ERROR", logger="next.defer"),
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
            job, Rendered("R", Delta(), effects=Effects(session_modified=True))
        )
        with (
            caplog.at_level("WARNING", logger="next.defer"),
            patch.object(boundary_state_written, "has_listeners", return_value=False),
            patch.object(boundary_state_written, "send_robust") as send,
            patch.object(boundary_state_written, "asend_robust") as asend,
        ):
            if where == "loop":
                asyncio.run(outcomes.atake(written))
            else:
                outcomes.take(written)
        send.assert_not_called()
        asend.assert_not_called()
        assert "wrote the session, which a job cannot do" in caplog.text
        assert "defer='inline' at the call site" in caplog.text

    def test_the_loop_announces_an_outcome_and_hops_for_an_error_ui(self) -> None:
        session = open_session()
        first, second = make_job(session), make_job(session)
        session.queue(first)
        session.queue(second)
        outcomes = Outcomes(session, None, streamed=True)
        written = Rendered("R", Delta(), effects=Effects(session_modified=True))

        async def take() -> tuple[object, object]:
            one = await outcomes.atake(Outcome(first, written))
            two = await outcomes.atake(Outcome(second, settled=ErrorKind.TIMEOUT))
            return one, two

        with capture_signals(boundary_settled, boundary_state_written) as recorder:
            one, two = asyncio.run(take())
        assert one.rendered.html == "R"
        assert two.rendered.html == "F:timeout"
        names = [event.signal for event in recorder.events]
        assert names.count(boundary_settled) == 2
        assert names.count(boundary_state_written) == 1
