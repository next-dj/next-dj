import contextvars
import logging
import threading
import time
from concurrent.futures import wait
from unittest import mock
from unittest.mock import Mock

import pytest
from asgiref.local import Local
from django.utils import translation

from next.boundaries.failures import ErrorKind
from next.boundaries.options import BoundaryOptions
from next.bridge.context import ContextSnapshot
from next.bridge.deadline import remaining
from next.bridge.tasks import StealableTask
from next.defer import Admit, Job
from next.defer.coordinator import (
    IDLE,
    Abandon,
    Coordinator,
    Emit,
    Machine,
    ProtocolError,
    Task,
    ThreadDriver,
    Unit,
)
from next.defer.coordinator.drivers import base
from next.defer.errors import ExecutorStoppedError
from next.defer.executors import BoundaryExecutor
from next.defer.ledger import ResponseJobs
from next.defer.markers import Group, Lane, LaneChoice, LaneRule, PoolVerdict, Rendered
from next.deps import Loader
from tests.defer.coordinator.drivers.doubles import (
    ATOMIC_WSGI,
    RUNNING_TIMEOUT,
    WAIT,
    Body,
    EagerPool,
    FailingAdmission,
    FullPool,
    HeldPool,
    IdleFullPool,
    IdlePool,
    RejectingPool,
    ScriptedAdmission,
    StartedPool,
    StoppedPool,
    StoppingIdlePool,
    close_scope,
    coordinator_over,
    error_ui,
    gated_body,
    html_of,
    ident_body,
    ids_of,
    items_of,
    loading_body,
    meeting_body,
    named_site,
    new_session,
    noop,
    queue_job,
    ready_body,
    recorded_fetch,
    scoped_session,
    sleeping_body,
    timings_of,
)
from tests.support.waits import QUIET


marker = contextvars.ContextVar[str]("marker", default="unset")
request_local = Local()


def stalled(_captured: ContextSnapshot) -> contextvars.Context:
    """Never restore onto a sync thread before the deadline."""
    raise TimeoutError


class TestThreadDriver:
    """Tests for ``Coordinator.run`` on the request thread."""

    def test_three_jobs_run_in_parallel_on_the_pool(self, pools) -> None:
        session = new_session()
        barrier = threading.Barrier(3, timeout=WAIT)
        jobs = [queue_job(session, meeting_body(barrier)) for _ in range(3)]
        executor = pools()
        batches = list(coordinator_over(executor).run(session))
        assert executor.submitted == 3
        assert sorted(items_of(batches)) == sorted(job.id for job in jobs)
        assert all(item.rendered is not None for item in items_of(batches).values())
        assert len({html_of(item) for item in items_of(batches).values()}) == 3

    def test_pool_jobs_read_the_language_of_the_request(self, pools) -> None:
        def body() -> Rendered:
            return Rendered(translation.get_language(), "")

        executor = pools()
        with translation.override("de"):
            session = new_session()
            queue_job(session, body)
            queue_job(session, body)
            batches = list(coordinator_over(executor).run(session))
        assert executor.submitted == 2
        assert [html_of(item) for item in items_of(batches).values()] == ["de", "de"]

    def test_pool_jobs_read_a_local_of_the_request(self, pools) -> None:
        def body() -> Rendered:
            return Rendered(str(getattr(request_local, "value", None)), "")

        def scenario() -> list[str]:
            request_local.value = "set"
            session = new_session()
            queue_job(session, body)
            queue_job(session, body)
            batches = list(coordinator_over(pools()).run(session))
            return [html_of(item) for item in items_of(batches).values()]

        assert contextvars.copy_context().run(scenario) == ["set", "set"]

    def test_a_job_on_the_thread_of_its_snapshot_restores_nothing(
        self, pools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = new_session()
        job = queue_job(session, ident_body, parallel=False)
        monkeypatch.setattr(ContextSnapshot, "restore", stalled)
        (batch,) = list(coordinator_over(pools()).run(session))
        assert batch.items[0].id == job.id
        assert html_of(batch.items[0]) == str(threading.get_ident())

    def test_a_rule_runs_every_job_on_the_request_thread_in_document_order(
        self, pools
    ) -> None:
        session = new_session(facts=ATOMIC_WSGI)
        order: list[tuple[str, int]] = []

        def body(name: str) -> Body:
            def run() -> Rendered:
                order.append((name, threading.get_ident()))
                return Rendered(name, "")

            return run

        for name in "abc":
            queue_job(session, body(name))
        executor = pools()
        batches = list(coordinator_over(executor).run(session))
        assert order == [(name, threading.get_ident()) for name in "abc"]
        assert executor.submitted == 0
        assert {timing.placement for timing in timings_of(batches).values()} == {
            LaneChoice(LaneRule.ATOMIC)
        }

    def test_a_buffered_run_steals_from_a_saturated_pool(self) -> None:
        session = new_session()
        for _ in range(3):
            queue_job(session, ident_body)
        executor = StartedPool()
        coordinator = coordinator_over(executor)
        try:
            batches = list(coordinator.run(session, buffered=True))
        finally:
            executor.join()
        assert executor.queued == []
        idents = {html_of(item) for item in items_of(batches).values()}
        assert str(threading.get_ident()) in idents
        lanes = {timing.lane for timing in timings_of(batches).values()}
        assert lanes == {Lane.POOL, Lane.REQUEST}

    def test_a_stream_steals_nothing_while_its_own_unit_runs(self) -> None:
        log: list[str] = []

        def running() -> Rendered:
            log.append("running")
            time.sleep(QUIET)
            log.append("ran")
            return Rendered("running", "")

        def queued() -> Rendered:
            log.append("stolen")
            return Rendered("queued", "")

        session = new_session()
        queue_job(session, running)
        queue_job(session, queued)
        executor = StartedPool()
        try:
            list(coordinator_over(executor).run(session))
        finally:
            executor.join()
        assert log == ["running", "ran", "stolen"]

    def test_a_stream_leaves_queued_units_to_free_workers(self) -> None:
        release = threading.Event()

        def slow() -> Rendered:
            release.wait(WAIT)
            return Rendered("slow", "")

        session = new_session()
        slow_job = queue_job(session, slow)
        fast_job = queue_job(session, ready_body("fast"))
        executor = IdlePool(2)

        def worker() -> None:
            executor.submitted.wait(WAIT)
            slowest, fast = executor.queued
            fast.run()
            release.wait(WAIT)
            slowest.run()

        thread = threading.Thread(target=worker)
        thread.start()
        stream = coordinator_over(executor).run(session)
        try:
            assert ids_of([next(stream)]) == [[fast_job.id]]
            release.set()
            assert ids_of(list(stream)) == [[slow_job.id]]
        finally:
            release.set()
            thread.join(WAIT)

    def test_a_timed_out_job_settles_through_its_error_ui(self, pools) -> None:
        started = threading.Event()
        release = threading.Event()
        done = threading.Event()
        gated = gated_body(started, release)

        def slow() -> Rendered:
            rendered = gated()
            done.set()
            return rendered

        session = new_session()
        job = queue_job(session, slow, timeout=RUNNING_TIMEOUT)
        coordinator = coordinator_over(pools())
        (batch,) = list(coordinator.run(session))
        assert started.wait(WAIT)
        assert not done.is_set()
        release.set()
        (item,) = batch.items
        assert item.rendered is None
        assert item.kind is ErrorKind.TIMEOUT
        assert item.job.error_ui(item.settled).html == "error:timeout"
        assert done.wait(WAIT)
        assert coordinator.totals is not None
        assert coordinator.totals.timeouts == 1
        assert coordinator.totals.late == 1
        assert item.timing.lane is Lane.POOL
        assert item.id == job.id

    def test_a_shed_job_settles_overloaded(self, pools) -> None:
        session = new_session()
        queue_job(session, ready_body("a"))
        queue_job(session, ready_body("b"))
        coordinator = coordinator_over(pools(), admission=ScriptedAdmission(Admit.SHED))
        items = items_of(list(coordinator.run(session)))
        assert {item.kind for item in items.values()} == {ErrorKind.OVERLOADED}
        assert coordinator.totals is not None
        assert coordinator.totals.shed == 2

    def test_a_group_reports_each_member_in_one_batch(self, pools) -> None:
        session = new_session(per_site=1)
        site = named_site("row")
        jobs = [queue_job(session, ident_body, site=site) for _ in range(4)]
        assert [type(unit) for unit in session.plan().units] == [Job, Group]
        batches = list(coordinator_over(pools()).run(session))
        assert [jobs[1].id, jobs[2].id, jobs[3].id] in ids_of(batches)
        members = [html_of(items_of(batches)[job.id]) for job in jobs[1:]]
        assert len(set(members)) == 1
        assert set(timings_of(batches)) == {job.id for job in jobs}

    def test_a_group_member_past_its_deadline_never_starts(self, pools) -> None:
        ran: list[str] = []

        def late() -> Rendered:
            ran.append("late")
            return Rendered("late", "")

        session = new_session(per_site=1)
        site = named_site("row")
        queue_job(session, ready_body("first"), site=site)
        queue_job(session, sleeping_body(0.15), site=site)
        skipped = queue_job(session, late, site=site, timeout=0.05)
        items = items_of(list(coordinator_over(pools()).run(session)))
        assert items[skipped.id].kind is ErrorKind.TIMEOUT
        assert ran == []

    def test_a_job_whose_snapshot_misses_its_deadline_never_starts(
        self, pools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ran: list[str] = []

        def late() -> Rendered:
            ran.append("late")
            return Rendered("late", "")

        session = new_session()
        job = queue_job(session, late)
        monkeypatch.setattr(ContextSnapshot, "restore", stalled)
        items = items_of(list(coordinator_over(pools()).run(session)))
        assert items[job.id].kind is ErrorKind.TIMEOUT
        assert ran == []

    def test_parents_are_written_before_the_jobs_they_nest(self, pools) -> None:
        session = new_session()
        parent_id = session.next_id()
        child = Job(
            session.next_id(),
            "k",
            named_site("child"),
            BoundaryOptions(),
            session.deadline,
            error_ui,
            run=ready_body("child"),
            parent=parent_id,
            depth=1,
        )

        def parent() -> Rendered:
            return Rendered("parent", "", nested=(child,))

        session.queue(
            Job(
                parent_id,
                "k",
                named_site("parent"),
                BoundaryOptions(),
                session.deadline,
                error_ui,
                run=parent,
            )
        )
        batches = list(coordinator_over(pools()).run(session))
        assert ids_of(batches) == [[parent_id], [child.id]]

    def test_a_quiet_wait_yields_idle_batches_that_acknowledge_nothing(
        self, pools
    ) -> None:
        started = threading.Event()
        release = threading.Event()
        session = new_session()
        fast = queue_job(session, ready_body("fast"))
        held = queue_job(session, gated_body(started, release))
        coordinator = coordinator_over(pools())
        emits: list[Emit] = []
        quiet: list[tuple[int, int]] = []
        with mock.patch.object(
            Machine, "written", autospec=True, side_effect=Machine.written
        ) as written:
            for batch in coordinator.run(session, idle=0.02):
                if isinstance(batch, Emit):
                    emits.append(batch)
                    continue
                assert batch is IDLE
                quiet.append((len(emits), written.call_count))
                if emits and started.is_set():
                    release.set()
        assert ids_of(emits) == [[fast.id], [held.id]]
        assert written.call_count == len(emits)
        assert (1, 1) in quiet
        assert all(written_before == seen for seen, written_before in quiet)
        assert IDLE.items == ()

    def test_without_idle_no_idle_batch_is_yielded(self, pools) -> None:
        session = new_session()
        queue_job(session, sleeping_body(QUIET))
        batches = list(coordinator_over(pools()).run(session))
        assert IDLE not in batches
        assert len(batches) == 1

    def test_closing_mid_stream_drops_queued_units_and_abandons_running_ones(
        self,
    ) -> None:
        ran: list[str] = []

        def body(name: str) -> Body:
            def run() -> Rendered:
                ran.append(name)
                return Rendered(name, "")

            return run

        session = new_session()
        first = queue_job(session, body("first"), parallel=False)
        queue_job(session, body("queued"))
        queue_job(session, body("running"))
        executor = HeldPool()
        coordinator = coordinator_over(executor)
        stream = coordinator.run(session)
        assert ids_of([next(stream)]) == [[first.id]]
        running = executor.start(1)
        closing = threading.Thread(target=stream.close)
        closing.start()
        closing.join(WAIT)
        assert not closing.is_alive()
        assert ran == ["first"]
        assert executor.queued == []
        assert running in executor.asked
        assert coordinator.finish is not None
        assert coordinator.finish.aborted
        assert coordinator.finish.totals.abandoned == 2

    def test_a_job_raising_system_exit_off_the_request_thread_fails_alone(
        self, pools
    ) -> None:
        def leave() -> Rendered:
            raise SystemExit(3)

        executor = pools()
        session = new_session()
        job = queue_job(session, leave)
        item = items_of(list(coordinator_over(executor).run(session)))[job.id]
        assert item.rendered is None
        assert item.kind is ErrorKind.ERROR
        assert isinstance(item.error, RuntimeError)
        assert isinstance(item.error.__cause__, SystemExit)
        again = new_session()
        queue_job(again, ready_body("after"))
        queue_job(again, ready_body("again"))
        items = items_of(list(coordinator_over(executor).run(again)))
        assert all(item.rendered is not None for item in items.values())

    @pytest.mark.parametrize("interrupt", [SystemExit(3), KeyboardInterrupt()])
    def test_an_interrupt_on_the_request_thread_ends_the_run(
        self, interrupt: BaseException
    ) -> None:
        def leave() -> Rendered:
            raise interrupt

        session = new_session()
        queue_job(session, leave)
        queue_job(session, ready_body("held"))
        coordinator = coordinator_over(HeldPool())
        with pytest.raises(type(interrupt)):
            list(coordinator.run(session))
        assert coordinator.finish is not None
        assert coordinator.finish.aborted
        assert coordinator.failed is False

    def test_a_failing_job_settles_as_an_error_with_its_exception(self, pools) -> None:
        def fail() -> Rendered:
            msg = "job failed"
            raise ValueError(msg)

        session = new_session()
        jobs = [queue_job(session, fail), queue_job(session, fail, parallel=False)]
        items = items_of(list(coordinator_over(pools()).run(session)))
        for job in jobs:
            item = items[job.id]
            assert item.rendered is None
            assert item.kind is ErrorKind.ERROR
            assert isinstance(item.error, ValueError)

    def test_jobs_run_in_isolated_contexts(self, pools) -> None:
        written = threading.Event()

        def write() -> Rendered:
            marker.set("written")
            written.set()
            return Rendered(marker.get(), "")

        def read() -> Rendered:
            assert written.wait(WAIT)
            return Rendered(marker.get(), "")

        session = new_session()
        writer = queue_job(session, write)
        reader = queue_job(session, read)
        items = items_of(list(coordinator_over(pools()).run(session)))
        assert html_of(items[writer.id]) == "written"
        assert html_of(items[reader.id]) == "unset"
        assert marker.get() == "unset"

    def test_remaining_reads_the_deadline_of_the_job(self, pools) -> None:
        read: list[float] = []

        def left() -> Rendered:
            before = time.monotonic()
            read.append(remaining() or 0.0)
            read.extend((before, time.monotonic()))
            return Rendered("read", "")

        session = new_session(deadline=60.0)
        job = queue_job(session, left, timeout=30.0)
        list(coordinator_over(pools()).run(session))
        assert job.deadline < session.deadline
        value, before, after = read
        assert job.deadline - after <= value <= job.deadline - before

    def test_a_full_queue_hands_its_units_to_the_request_thread(self) -> None:
        session = new_session()
        jobs = [queue_job(session, ident_body) for _ in range(2)]
        executor = FullPool()
        coordinator = coordinator_over(executor)
        batches = list(coordinator.run(session))
        items = items_of(batches)
        assert [html_of(items[job.id]) for job in jobs] == [
            str(threading.get_ident())
        ] * 2
        assert {timing.lane for timing in timings_of(batches).values()} == {
            Lane.REQUEST
        }
        assert executor.refused == 2
        assert coordinator.totals is not None
        assert coordinator.totals.shed == 0

    @pytest.mark.parametrize(
        "pool",
        [RejectingPool, StoppedPool],
        ids=["rejects_the_unit", "refuses_to_take_it"],
    )
    def test_a_unit_the_pool_turns_away_fails_its_jobs(
        self, pool: type[HeldPool]
    ) -> None:
        session = new_session()
        job = queue_job(session, ready_body("never"))
        queue_job(session, ready_body("never"))
        item = items_of(list(coordinator_over(pool()).run(session)))[job.id]
        assert item.rendered is None
        assert isinstance(item.error, ExecutorStoppedError)

    def test_a_capacity_reading_admits_queued_units_again(self) -> None:
        session = new_session(deadline=0.2, per_request=1)
        held = queue_job(session, ready_body("held"))
        shed = queue_job(session, ready_body("shed"))
        executor = HeldPool(stealable=False)
        coordinator = coordinator_over(
            executor, admission=ScriptedAdmission(Admit.POOL, Admit.SHED)
        )
        batches = list(coordinator.run(session))
        assert ids_of(batches) == [[shed.id], [held.id]]
        assert items_of(batches)[held.id].kind is ErrorKind.TIMEOUT

    def test_a_hung_job_frees_the_request_thread_at_the_deadline_and_grace(
        self, pools
    ) -> None:
        started, release, done = threading.Event(), threading.Event(), threading.Event()
        gated = gated_body(started, release)

        def hung() -> Rendered:
            rendered = gated()
            done.set()
            return rendered

        session = new_session(deadline=0.2, grace=0.1)
        job = queue_job(session, hung)
        coordinator = coordinator_over(pools())
        try:
            batches = list(coordinator.run(session))
            freed_while_hung = not done.is_set()
        finally:
            release.set()
        assert freed_while_hung
        assert started.is_set()
        assert items_of(batches)[job.id].kind is ErrorKind.TIMEOUT
        assert coordinator.finish is not None
        assert not coordinator.finish.aborted

    def test_an_empty_session_finishes_without_a_batch(self, pools) -> None:
        coordinator = coordinator_over(pools())
        assert list(coordinator.run(new_session())) == []
        assert coordinator.finish is not None
        assert not coordinator.finish.aborted
        assert coordinator.failed is False

    def test_missing_facts_and_snapshot_fall_back_to_the_calling_thread(
        self, pools
    ) -> None:
        def read() -> Rendered:
            return Rendered(marker.get(), "")

        def scenario() -> list[Emit]:
            marker.set("caller")
            session = new_session(facts=None)
            session.snapshot = None
            queue_job(session, read)
            return list(coordinator_over(pools()).run(session))

        (batch,) = contextvars.copy_context().run(scenario)
        assert html_of(batch.items[0]) == "caller"

    def test_the_process_backends_serve_a_bare_coordinator(self) -> None:
        session = new_session()
        job = queue_job(session, ident_body)
        coordinator: Coordinator = Coordinator()
        (batch,) = list(coordinator.run(session))
        (item,) = batch.items
        assert html_of(item) != str(threading.get_ident())
        assert (item.id, item.timing.lane) == (job.id, Lane.POOL)

    @pytest.mark.parametrize("ending", ["returns", "raises"])
    def test_a_batch_closes_only_once_a_job_not_loading_ends(
        self, pools, ending: str
    ) -> None:
        log: list[object] = []
        loader = Loader(recorded_fetch(log), window=3600)
        barrier = threading.Barrier(2, timeout=WAIT)

        def other() -> Rendered:
            barrier.wait()
            time.sleep(QUIET)
            log.append("ended")
            if ending == "raises":
                msg = "other failed"
                raise RuntimeError(msg)
            return Rendered("other", "")

        session = new_session()
        scope = scoped_session(session)
        queue_job(session, other)
        loading = queue_job(session, loading_body(loader, 1, barrier))
        try:
            items = items_of(list(coordinator_over(pools()).run(session)))
        finally:
            close_scope(scope)
        assert html_of(items[loading.id]) == "10"
        assert log == ["ended", [1]]

    def test_a_job_past_its_deadline_frees_the_batch_it_held_open(self, pools) -> None:
        log: list[object] = []
        loader = Loader(recorded_fetch(log), window=3600)
        barrier = threading.Barrier(2, timeout=WAIT)
        release = threading.Event()

        def blocked() -> Rendered:
            barrier.wait()
            release.wait(WAIT)
            return Rendered("late", "")

        session = new_session()
        scope = scoped_session(session)
        stuck = queue_job(session, blocked, timeout=RUNNING_TIMEOUT)
        loading = queue_job(session, loading_body(loader, 1, barrier))
        try:
            items = items_of(list(coordinator_over(pools()).run(session)))
        finally:
            release.set()
            close_scope(scope)
        assert items[stuck.id].kind is ErrorKind.TIMEOUT
        assert html_of(items[loading.id]) == "10"
        assert log == [[1]]

    def test_jobs_the_request_thread_steals_close_their_own_batches(self) -> None:
        log: list[object] = []
        loader = Loader(recorded_fetch(log), window=3600)
        session = new_session()
        scope = scoped_session(session)
        first = queue_job(session, loading_body(loader, 1))
        second = queue_job(session, loading_body(loader, 2))
        try:
            items = items_of(list(coordinator_over(HeldPool()).run(session)))
        finally:
            close_scope(scope)
        assert [html_of(items[job.id]) for job in (first, second)] == ["10", "20"]
        assert log == [[1], [2]]

    def test_a_pool_job_no_worker_started_yet_holds_the_batch_open(self) -> None:
        log: list[object] = []
        loader = Loader(recorded_fetch(log), window=3600)
        executor = IdlePool(2)
        held: list[bool] = []

        def worker() -> None:
            executor.submitted.wait(WAIT)
            first, second = executor.queued
            parked = threading.Thread(target=first.run)
            parked.start()
            held.append(not wait((first.future,), timeout=QUIET).done)
            second.run()
            parked.join(WAIT)

        session = new_session()
        scope = scoped_session(session)
        first = queue_job(session, loading_body(loader, 1))
        second = queue_job(session, loading_body(loader, 2))
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            items = items_of(list(coordinator_over(executor).run(session)))
        finally:
            thread.join(WAIT)
            close_scope(scope)
        assert [html_of(items[job.id]) for job in (first, second)] == ["10", "20"]
        assert held == [True]
        assert log == [[1, 2]]

    def test_a_job_parked_before_its_sibling_is_submitted_still_shares_its_batch(
        self,
    ) -> None:
        log: list[object] = []
        loader = Loader(recorded_fetch(log), window=3600)
        executor = EagerPool()
        session = new_session()
        scope = scoped_session(session)
        first = queue_job(session, loading_body(loader, 1))
        second = queue_job(session, loading_body(loader, 2))
        try:
            items = items_of(list(coordinator_over(executor).run(session)))
        finally:
            executor.join()
            close_scope(scope)
        assert [html_of(items[job.id]) for job in (first, second)] == ["10", "20"]
        assert executor.held == [True]
        assert log == [[1, 2]]

    def test_request_lane_jobs_queued_behind_a_parked_one_leave_its_batch(self) -> None:
        log: list[object] = []
        loader = Loader(recorded_fetch(log), window=3600)
        session = new_session(facts=ATOMIC_WSGI)
        scope = scoped_session(session)
        first = queue_job(session, loading_body(loader, 1))
        second = queue_job(session, loading_body(loader, 2))
        try:
            items = items_of(list(coordinator_over(HeldPool()).run(session)))
        finally:
            close_scope(scope)
        assert [html_of(items[job.id]) for job in (first, second)] == ["10", "20"]
        assert log == [[1], [2]]

    def test_a_unit_counted_on_admission_that_the_queue_sheds_leaves_its_count(
        self,
    ) -> None:
        log: list[object] = []
        loader = Loader(recorded_fetch(log), window=3600)
        session = new_session()
        scope = scoped_session(session)
        job = queue_job(session, loading_body(loader, 1))
        try:
            items = items_of(list(coordinator_over(IdleFullPool()).run(session)))
        finally:
            close_scope(scope)
        assert html_of(items[job.id]) == "10"
        assert items[job.id].timing.lane is Lane.REQUEST
        assert log == [[1]]

    def test_a_unit_counted_on_admission_that_the_pool_refuses_leaves_its_count(
        self,
    ) -> None:
        log: list[object] = []
        loader = Loader(recorded_fetch(log), window=3600)
        executor = StoppingIdlePool(1)

        def worker() -> None:
            executor.submitted.wait(WAIT)
            executor.queued[0].run()

        session = new_session()
        scope = scoped_session(session)
        refused = queue_job(session, loading_body(loader, 1))
        loading = queue_job(session, loading_body(loader, 2))
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            items = items_of(list(coordinator_over(executor).run(session)))
        finally:
            thread.join(WAIT)
            close_scope(scope)
        assert items[refused.id].kind is ErrorKind.OVERLOADED
        assert html_of(items[loading.id]) == "20"
        assert log == [[2]]

    def test_a_parked_job_of_a_closed_stream_ends_with_its_scope(self, pools) -> None:
        loader = Loader(recorded_fetch([]), window=3600)
        flying = threading.Event()
        barrier = threading.Barrier(2, action=flying.set, timeout=WAIT)
        release = threading.Event()
        ended = threading.Event()

        def parked() -> Rendered:
            try:
                return loading_body(loader, 1, barrier)()
            finally:
                ended.set()

        def blocked() -> Rendered:
            barrier.wait()
            release.wait(WAIT)
            return Rendered("late", "")

        session = new_session()
        scope = scoped_session(session)
        queue_job(session, ready_body("first"))
        queue_job(session, parked)
        queue_job(session, blocked)
        stream = coordinator_over(pools()).run(session)
        try:
            next(stream)
            assert flying.wait(WAIT)
            stream.close()
            assert not ended.wait(QUIET)
            close_scope(scope)
            assert ended.wait(WAIT)
        finally:
            release.set()

    def test_a_machinery_failure_is_logged_once_and_ends_the_stream(
        self, pools, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = new_session()
        queue_job(session, ready_body("a"))
        queue_job(session, ready_body("b"))
        coordinator = coordinator_over(pools(), admission=FailingAdmission())
        with caplog.at_level(logging.ERROR, logger="next.defer"):
            assert list(coordinator.run(session)) == []
        failures = [r for r in caplog.records if r.getMessage() == base.FAILED]
        assert len(failures) == 1
        assert coordinator.finish is not None
        assert coordinator.finish.aborted
        assert coordinator.failed is True

    def test_a_failure_before_the_machine_exists_ends_the_stream(
        self, pools, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def broken(self: ResponseJobs) -> None:
            msg = "plan broke"
            raise RuntimeError(msg)

        monkeypatch.setattr(ResponseJobs, "plan", broken)
        session = new_session()
        coordinator = coordinator_over(pools())
        with caplog.at_level(logging.ERROR, logger="next.defer"):
            assert list(coordinator.run(session)) == []
        assert coordinator.finish is None
        assert any(r.getMessage() == base.FAILED for r in caplog.records)

    def test_a_failing_cancel_still_publishes_an_aborted_finish(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def broken(self: Machine, now: float) -> None:
            msg = "cancel broke"
            raise ProtocolError(msg)

        monkeypatch.setattr(Machine, "cancel", broken)
        session = new_session()
        queue_job(session, ready_body("first"), parallel=False)
        queue_job(session, ready_body("held"))
        coordinator = coordinator_over(HeldPool(stealable=False))
        stream = coordinator.run(session)
        next(stream)
        with caplog.at_level(logging.ERROR, logger="next.defer"):
            stream.close()
        assert [r.getMessage() for r in caplog.records] == [base.FAILED]
        assert coordinator.finish is not None
        assert coordinator.finish.aborted
        assert coordinator.failed is True

    def test_a_failure_and_a_failing_cancel_log_once(
        self, pools, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def broken(self: Machine, now: float) -> None:
            msg = "cancel broke"
            raise ProtocolError(msg)

        monkeypatch.setattr(Machine, "cancel", broken)
        session = new_session()
        queue_job(session, ready_body("a"))
        coordinator = coordinator_over(pools(), admission=FailingAdmission())
        with caplog.at_level(logging.ERROR, logger="next.defer"):
            list(coordinator.run(session))
        assert [r.getMessage() for r in caplog.records] == [base.FAILED]

    def test_an_error_the_consumer_throws_cancels_and_propagates(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = new_session()
        queue_job(session, ready_body("first"), parallel=False)
        queue_job(session, ready_body("held"))
        coordinator = coordinator_over(HeldPool(stealable=False))
        stream = coordinator.run(session)
        next(stream)
        with pytest.raises(ValueError, match="consumer broke"):
            stream.throw(ValueError("consumer broke"))
        assert coordinator.finish is not None
        assert coordinator.finish.aborted
        assert not caplog.records

    def test_an_abandon_of_an_ended_unit_leaves_its_work_alone(self) -> None:
        executor = Mock(spec=BoundaryExecutor)
        session = new_session()
        job = queue_job(session, ready_body("a"))
        driver = ThreadDriver(coordinator_over(executor), session)
        unit = Unit("u", (Task(job, job.deadline),), LaneChoice(PoolVerdict.POOL))
        run = driver._register(unit, Lane.POOL)
        run.work = StealableTask(noop)
        driver._shut(run)
        driver._command(Abandon(("u",)))
        executor.steal.assert_not_called()
        assert not run.abandoned

    @pytest.mark.parametrize("method", ["_command", "_receive"])
    def test_a_command_or_message_of_no_known_kind_is_refused(
        self, method: str
    ) -> None:
        driver = ThreadDriver(coordinator_over(HeldPool()), new_session())
        with pytest.raises(AssertionError):
            getattr(driver, method)(object())
        assert driver._finish is None
