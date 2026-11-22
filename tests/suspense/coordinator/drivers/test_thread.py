import asyncio
import contextvars
import logging
import threading
import time
from concurrent.futures import Future
from unittest import mock
from unittest.mock import Mock

import pytest
from django.core.exceptions import ImproperlyConfigured

from next.defer import BoundaryOptions, ErrorKind, remaining
from next.deps import Loader
from next.deps.cache import _CACHE_MISS, RequestMemo, request_memo
from next.deps.scope import RequestScope
from next.suspense import (
    Admit,
    BoundaryExecutor,
    ExecutorStoppedError,
    Job,
    Lane,
    Placement,
    Rule,
    Verdict,
)
from next.suspense.coordinator import (
    COSTS,
    IDLE,
    Abandon,
    Coordinator,
    Emit,
    Machine,
    ProtocolError,
    Settlement,
    ThreadDriver,
    Unit,
    drivers,
    runner,
)
from next.suspense.executors import RequestThreadExecutor
from next.suspense.markers import Group, Outcome, Rendered
from next.suspense.session import BoundarySession
from next.tasks import StealableTask
from tests.support.waits import QUIET
from tests.suspense.coordinator.drivers.doubles import (
    ADAPTIVE,
    ATOMIC_WSGI,
    RUNNING_TIMEOUT,
    WAIT,
    Body,
    FailingAdmission,
    FullPool,
    HeldPool,
    IdlePool,
    RejectingPool,
    ScriptedAdmission,
    StartedPool,
    StoppedPool,
    close_scope,
    coordinator_over,
    fallback_ui,
    forever_body,
    gated_body,
    hop_body,
    html_of,
    ident_body,
    ids_of,
    items_of,
    loading_body,
    loop_tasks,
    meeting_body,
    named_site,
    new_session,
    noop,
    queue_job,
    ready_body,
    recorded_fetch,
    scoped_session,
    sleeping_body,
)


marker = contextvars.ContextVar[str]("marker", default="unset")


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
        assert all(isinstance(item, Outcome) for item in items_of(batches).values())
        assert len({html_of(item) for item in items_of(batches).values()}) == 3

    def test_a_lone_job_runs_on_the_request_thread_without_a_submission(
        self, pools
    ) -> None:
        session = new_session()
        job = queue_job(session, ident_body)
        executor = pools()
        coordinator = coordinator_over(executor, thresholds=ADAPTIVE)
        (batch,) = list(coordinator.run(session))
        assert html_of(batch.items[0]) == str(threading.get_ident())
        assert executor.submitted == 0
        assert coordinator.timings[job.id].lane is Lane.REQUEST
        assert coordinator.timings[job.id].placement == Placement(Verdict.ALONE)

    def test_a_rule_runs_every_job_on_the_request_thread_in_document_order(
        self, pools
    ) -> None:
        session = new_session(facts=ATOMIC_WSGI)
        order: list[tuple[str, int]] = []

        def body(name: str) -> Body:
            def run() -> Rendered[str]:
                order.append((name, threading.get_ident()))
                return Rendered(name, "")

            return run

        for name in "abc":
            queue_job(session, body(name))
        executor = pools()
        coordinator = coordinator_over(executor)
        list(coordinator.run(session))
        assert order == [(name, threading.get_ident()) for name in "abc"]
        assert executor.submitted == 0
        assert {timing.placement for timing in coordinator.timings.values()} == {
            Placement(Rule.ATOMIC)
        }

    def test_an_async_hop_lands_on_the_request_thread(self, pools) -> None:
        session = new_session()
        job = queue_job(session, abody=hop_body)
        (batch,) = list(coordinator_over(pools()).run(session))
        assert html_of(batch.items[0]) == str(threading.get_ident())
        assert batch.items[0].timing.lane is Lane.LOOP
        assert job.id == batch.items[0].id

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
        lanes = {timing.lane for timing in coordinator.timings.values()}
        assert lanes == {Lane.POOL, Lane.REQUEST}

    def test_a_stream_steals_nothing_while_its_own_unit_runs(self) -> None:
        log: list[str] = []

        def running() -> Rendered[str]:
            log.append("running")
            time.sleep(QUIET)
            log.append("ran")
            return Rendered("running", "")

        def queued() -> Rendered[str]:
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

    def test_a_stream_steals_nothing_while_its_async_unit_runs(self) -> None:
        settled = threading.Event()
        seen: list[bool] = []

        async def slow() -> Rendered[str]:
            await asyncio.sleep(QUIET)
            settled.set()
            return Rendered("slow", "")

        def pooled() -> Rendered[str]:
            seen.append(settled.is_set())
            return Rendered("pooled", "")

        session = new_session()
        queue_job(session, abody=slow)
        queue_job(session, pooled)
        list(coordinator_over(HeldPool()).run(session))
        assert seen == [True]

    def test_a_stream_leaves_queued_units_to_free_workers(self) -> None:
        release = threading.Event()

        def slow() -> Rendered[str]:
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

    def test_a_timed_out_job_settles_through_its_fallback(self, pools) -> None:
        started = threading.Event()
        release = threading.Event()
        done = threading.Event()
        gated = gated_body(started, release)

        def slow() -> Rendered[str]:
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
        assert isinstance(item, Settlement)
        assert item.kind is ErrorKind.TIMEOUT
        assert item.job.fallback(item.kind).html == "fallback:timeout"
        assert done.wait(WAIT)
        assert coordinator.totals is not None
        assert coordinator.totals.timeouts == 1
        assert coordinator.totals.late == 1
        assert coordinator.timings[job.id].lane is Lane.POOL

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
        coordinator = coordinator_over(pools())
        batches = list(coordinator.run(session))
        assert [jobs[1].id, jobs[2].id, jobs[3].id] in ids_of(batches)
        members = [html_of(items_of(batches)[job.id]) for job in jobs[1:]]
        assert len(set(members)) == 1
        assert set(coordinator.timings) == {job.id for job in jobs}

    def test_a_group_member_past_its_deadline_never_starts(self, pools) -> None:
        ran: list[str] = []

        def late() -> Rendered[str]:
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

    def test_parents_are_written_before_the_jobs_they_nest(self, pools) -> None:
        session = new_session()
        parent_id = session.next_id()
        child = Job(
            session.next_id(),
            "k",
            named_site("child"),
            BoundaryOptions(),
            session.deadline,
            fallback_ui,
            run=ready_body("child"),
            parent=parent_id,
            depth=1,
        )

        def parent() -> Rendered[str]:
            return Rendered("parent", "", nested=(child,))

        session.queue(
            Job(
                parent_id,
                "k",
                named_site("parent"),
                BoundaryOptions(),
                session.deadline,
                fallback_ui,
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
        emits: list[Emit[str]] = []
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
            def run() -> Rendered[str]:
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

    def test_closing_mid_stream_cancels_loop_tasks(self, pools) -> None:
        session = new_session()
        queue_job(session, ready_body("fast"), parallel=False)
        queue_job(session, abody=forever_body)
        coordinator = coordinator_over(pools())
        stream = coordinator.run(session)
        next(stream)
        stream.close()
        assert loop_tasks() == []
        assert coordinator.finish is not None
        assert coordinator.finish.totals.cancelled == 1

    def test_a_timed_out_async_job_is_cancelled(self, pools) -> None:
        session = new_session()
        job = queue_job(session, abody=forever_body, timeout=0.05)
        queue_job(session, sleeping_body(0.15))
        items = items_of(list(coordinator_over(pools()).run(session)))
        assert items[job.id].kind is ErrorKind.TIMEOUT
        assert loop_tasks() == []

    def test_a_job_raising_system_exit_off_the_request_thread_fails_alone(
        self, pools
    ) -> None:
        def leave() -> Rendered[str]:
            raise SystemExit(3)

        async def aleave() -> Rendered[str]:
            raise SystemExit(4)

        executor = pools()
        session = new_session()
        sync_job = queue_job(session, leave)
        async_job = queue_job(session, abody=aleave)
        items = items_of(list(coordinator_over(executor).run(session)))
        errors = []
        for job in (sync_job, async_job):
            item = items[job.id]
            assert isinstance(item, Settlement)
            assert item.kind is ErrorKind.ERROR
            errors.append(item.error)
        pooled, looped = errors
        assert isinstance(pooled, RuntimeError)
        assert isinstance(pooled.__cause__, SystemExit)
        assert isinstance(looped, SystemExit)
        again = new_session()
        queue_job(again, ready_body("after"))
        queue_job(again, abody=hop_body)
        items = items_of(list(coordinator_over(executor).run(again)))
        assert all(isinstance(item, Outcome) for item in items.values())

    @pytest.mark.parametrize("interrupt", [SystemExit(3), KeyboardInterrupt()])
    def test_an_interrupt_on_the_request_thread_ends_the_run(
        self, interrupt: BaseException
    ) -> None:
        def leave() -> Rendered[str]:
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
        def fail() -> Rendered[str]:
            msg = "job failed"
            raise ValueError(msg)

        async def afail() -> Rendered[str]:
            msg = "async job failed"
            raise ValueError(msg)

        session = new_session()
        jobs = [queue_job(session, fail), queue_job(session, abody=afail)]
        items = items_of(list(coordinator_over(pools()).run(session)))
        for job in jobs:
            item = items[job.id]
            assert isinstance(item, Settlement)
            assert item.kind is ErrorKind.ERROR
            assert isinstance(item.error, ValueError)

    def test_an_async_group_member_past_its_deadline_never_starts(self, pools) -> None:
        ran: list[str] = []

        async def first() -> Rendered[str]:
            return Rendered("first", "")

        async def slow() -> Rendered[str]:
            await asyncio.sleep(0.15)
            return Rendered("slow", "")

        async def late() -> Rendered[str]:
            ran.append("late")
            return Rendered("late", "")

        session = new_session(per_site=1)
        site = named_site("async-row")
        queue_job(session, abody=first, site=site)
        queue_job(session, abody=slow, site=site)
        skipped = queue_job(session, abody=late, site=site, timeout=0.05)
        items = items_of(list(coordinator_over(pools()).run(session)))
        assert items[skipped.id].kind is ErrorKind.TIMEOUT
        assert ran == []

    def test_an_async_job_cancelling_itself_fails(self, pools) -> None:
        async def cancel() -> Rendered[str]:
            raise asyncio.CancelledError

        session = new_session()
        job = queue_job(session, abody=cancel)
        item = items_of(list(coordinator_over(pools()).run(session)))[job.id]
        assert isinstance(item, Settlement)
        assert isinstance(item.error, asyncio.CancelledError)

    def test_an_unusable_asgiref_fails_async_jobs(
        self, pools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse() -> None:
            msg = "asgiref too old"
            raise ImproperlyConfigured(msg)

        monkeypatch.setattr(drivers, "check_asgiref", refuse)
        session = new_session()
        job = queue_job(session, abody=hop_body)
        item = items_of(list(coordinator_over(pools()).run(session)))[job.id]
        assert isinstance(item, Settlement)
        assert isinstance(item.error, ImproperlyConfigured)

    def test_jobs_run_in_isolated_contexts(self, pools) -> None:
        written = threading.Event()

        def write() -> Rendered[str]:
            marker.set("written")
            written.set()
            return Rendered(marker.get(), "")

        def read() -> Rendered[str]:
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

        def left() -> Rendered[str]:
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

    def test_a_run_feeds_the_cost_table(self, pools) -> None:
        session = new_session()
        site = named_site(f"costed-{session.seal}")
        queue_job(session, sleeping_body(0.01), site=site)
        list(coordinator_over(pools()).run(session))
        cost = COSTS.cost(site.cost_key)
        assert cost is not None
        assert cost.runs == 1
        assert cost.wall >= 10_000_000

    def test_a_full_queue_hands_its_units_to_the_request_thread(self) -> None:
        session = new_session()
        jobs = [queue_job(session, ident_body) for _ in range(2)]
        executor = FullPool()
        coordinator = coordinator_over(executor)
        items = items_of(list(coordinator.run(session)))
        assert [html_of(items[job.id]) for job in jobs] == [
            str(threading.get_ident())
        ] * 2
        assert {coordinator.timings[job.id].lane for job in jobs} == {Lane.REQUEST}
        assert executor.refused == 2
        assert coordinator.totals is not None
        assert coordinator.totals.shed == 0

    def test_hops_served_during_a_job_are_not_charged_to_its_site(
        self, pools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        served = iter([0, 10**15])
        monkeypatch.setattr(runner, "served_cpu", lambda: next(served))

        def burn() -> Rendered[str]:
            started = time.thread_time_ns()
            while time.thread_time_ns() == started:
                pass
            return Rendered("burnt", "")

        session = new_session()
        site = named_site(f"served-{session.seal}")
        queue_job(session, burn, site=site)
        list(coordinator_over(pools(), thresholds=ADAPTIVE).run(session))
        cost = COSTS.cost(site.cost_key)
        assert cost is not None
        assert cost.cpu == 0.0

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
        assert isinstance(item, Settlement)
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

    def test_an_empty_session_finishes_without_a_batch(self, pools) -> None:
        coordinator = coordinator_over(pools())
        assert list(coordinator.run(new_session())) == []
        assert coordinator.finish is not None
        assert not coordinator.finish.aborted
        assert coordinator.timings == {}
        assert coordinator.failed is False

    def test_missing_facts_and_snapshot_fall_back_to_the_calling_thread(
        self, pools
    ) -> None:
        def read() -> Rendered[str]:
            return Rendered(marker.get(), "")

        def scenario() -> list[Emit[str]]:
            marker.set("caller")
            session = new_session(facts=None)
            session.snapshot = None
            queue_job(session, read)
            return list(coordinator_over(pools(), thresholds=ADAPTIVE).run(session))

        (batch,) = contextvars.copy_context().run(scenario)
        assert html_of(batch.items[0]) == "caller"

    def test_the_process_backends_serve_a_bare_coordinator(self) -> None:
        session = new_session()
        queue_job(session, ident_body)
        coordinator: Coordinator[str] = Coordinator()
        (batch,) = list(coordinator.run(session))
        assert html_of(batch.items[0]) == str(threading.get_ident())

    def test_each_async_job_resolves_in_a_layer_of_the_request_scope(
        self, pools
    ) -> None:
        """A job reads what the shell left, and what it computes stays its own."""
        scope = RequestScope({"shell": "seeded"})
        calls: list[str] = []

        async def compute() -> object:
            calls.append("own")
            return "own"

        async def scoped() -> Rendered[str]:
            memo = request_memo.get()
            assert memo is not None
            assert memo.scope is not scope
            value = await memo.scope.resolve("own", compute)
            return Rendered(f"{memo.scope.get('shell')}-{value}", "")

        session = new_session()
        session.scope = scope
        jobs = [queue_job(session, abody=scoped) for _ in range(2)]
        items = items_of(list(coordinator_over(pools()).run(session)))
        assert [html_of(items[job.id]) for job in jobs] == ["seeded-own"] * 2
        assert calls == ["own", "own"]
        assert scope.get("own") is _CACHE_MISS

    def test_an_async_job_keeps_the_shares_of_a_memo_of_the_same_scope(
        self, pools
    ) -> None:
        scope = RequestScope({"shell": "seeded"})
        held = RequestMemo(scope, shared=frozenset({"key"}))

        async def scoped() -> Rendered[str]:
            memo = request_memo.get()
            assert memo is not None
            layered = memo.scope is not scope and memo.scope.get("shell") == "seeded"
            return Rendered(str(layered and memo.shared == held.shared), "")

        def scenario() -> list[Emit[str]]:
            request_memo.set(held)
            session = new_session()
            session.scope = scope
            queue_job(session, abody=scoped)
            return list(coordinator_over(pools()).run(session))

        (batch,) = contextvars.copy_context().run(scenario)
        assert html_of(batch.items[0]) == "True"

    @pytest.mark.parametrize("ending", ["returns", "raises"])
    def test_a_batch_closes_only_once_a_job_not_loading_ends(
        self, pools, ending: str
    ) -> None:
        log: list[object] = []
        loader = Loader(recorded_fetch(log), window=3600)
        barrier = threading.Barrier(2, timeout=WAIT)

        def other() -> Rendered[str]:
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

        def blocked() -> Rendered[str]:
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

    def test_a_parked_job_of_a_closed_stream_ends_with_its_scope(self, pools) -> None:
        loader = Loader(recorded_fetch([]), window=3600)
        flying = threading.Event()
        barrier = threading.Barrier(2, action=flying.set, timeout=WAIT)
        release = threading.Event()
        ended = threading.Event()

        def parked() -> Rendered[str]:
            try:
                return loading_body(loader, 1, barrier)()
            finally:
                ended.set()

        def blocked() -> Rendered[str]:
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
            assert not ended.wait(0.05)
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
        with caplog.at_level(logging.ERROR, logger="next.suspense"):
            assert list(coordinator.run(session)) == []
        failures = [r for r in caplog.records if r.getMessage() == drivers._FAILED]
        assert len(failures) == 1
        assert coordinator.finish is not None
        assert coordinator.finish.aborted
        assert coordinator.failed is True

    def test_a_failure_before_the_machine_exists_ends_the_stream(
        self, pools, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def broken(self: BoundarySession[str]) -> None:
            msg = "plan broke"
            raise RuntimeError(msg)

        monkeypatch.setattr(BoundarySession, "plan", broken)
        session = new_session()
        coordinator = coordinator_over(pools())
        with caplog.at_level(logging.ERROR, logger="next.suspense"):
            assert list(coordinator.run(session)) == []
        assert coordinator.finish is None
        assert any(r.getMessage() == drivers._FAILED for r in caplog.records)

    def test_a_failing_cancel_still_publishes_an_aborted_finish(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def broken(self: Machine[str], now: float) -> None:
            msg = "cancel broke"
            raise ProtocolError(msg)

        monkeypatch.setattr(Machine, "cancel", broken)
        session = new_session()
        queue_job(session, ready_body("first"), parallel=False)
        queue_job(session, ready_body("held"))
        coordinator = coordinator_over(HeldPool(stealable=False))
        stream = coordinator.run(session)
        next(stream)
        with caplog.at_level(logging.ERROR, logger="next.suspense"):
            stream.close()
        assert [r.getMessage() for r in caplog.records] == [drivers._FAILED]
        assert coordinator.finish is not None
        assert coordinator.finish.aborted
        assert coordinator.failed is True

    def test_a_failure_and_a_failing_cancel_log_once(
        self, pools, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def broken(self: Machine[str], now: float) -> None:
            msg = "cancel broke"
            raise ProtocolError(msg)

        monkeypatch.setattr(Machine, "cancel", broken)
        session = new_session()
        queue_job(session, ready_body("a"))
        coordinator = coordinator_over(pools(), admission=FailingAdmission())
        with caplog.at_level(logging.ERROR, logger="next.suspense"):
            list(coordinator.run(session))
        assert [r.getMessage() for r in caplog.records] == [drivers._FAILED]

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
        driver = ThreadDriver[str](coordinator_over(executor), new_session())
        unit = Unit("u", (), Placement(Verdict.POOL_MODE))
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
        driver = ThreadDriver[str](coordinator_over(HeldPool()), new_session())
        with pytest.raises(AssertionError):
            getattr(driver, method)(object())
        assert driver._finish is None

    def test_a_message_already_queued_skips_the_serving_wait(self) -> None:
        driver = ThreadDriver[str](coordinator_over(HeldPool()), new_session())
        hops = Mock(spec=RequestThreadExecutor)
        driver._hops = hops
        driver._inbox.put(drivers._TICK)
        driver._serve(time.monotonic() + WAIT)
        hops.serve.assert_not_called()
        assert driver._signal is None

    def test_the_serving_wait_ends_at_the_wake_instant(self) -> None:
        driver = ThreadDriver[str](coordinator_over(HeldPool()), new_session())
        driver._hops = RequestThreadExecutor()
        started = time.perf_counter()
        driver._serve(time.monotonic() + 0.01)
        assert time.perf_counter() - started < WAIT

    def test_a_report_wakes_the_serving_wait(self) -> None:
        driver = ThreadDriver[str](coordinator_over(HeldPool()), new_session())
        driver._hops = RequestThreadExecutor()
        timer = threading.Timer(0.01, driver._post, (drivers._TICK,))
        timer.start()
        started = time.perf_counter()
        driver._serve(time.monotonic() + 30)
        timer.join(5)
        assert time.perf_counter() - started < 5
        assert not driver._inbox.empty()

    def test_queued_hops_run_before_any_steal(self) -> None:
        driver = ThreadDriver[str](coordinator_over(HeldPool()), new_session())
        hops = RequestThreadExecutor()
        driver._hops = hops
        driver._background = Mock()
        ran: list[int] = []
        submitter = threading.Thread(target=lambda: hops.submit(ran.append, 1))
        submitter.start()
        submitter.join(5)
        driver._serve_queued()
        assert ran == [1]

    def test_ringing_a_done_signal_does_nothing(self) -> None:
        signal: Future[None] = Future()
        signal.set_result(None)
        drivers._ring(signal)
        assert signal.result() is None
