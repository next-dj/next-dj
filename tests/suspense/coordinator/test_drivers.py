import asyncio
import contextlib
import contextvars
import inspect
import logging
import threading
import time
from collections.abc import Callable, Coroutine, Iterator, Sequence
from concurrent.futures import Future
from typing import Any
from unittest import mock
from unittest.mock import Mock

import pytest
from asgiref.sync import ThreadSensitiveContext, sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from next.defer import BoundaryOptions, DeferMode, ErrorKind, remaining
from next.deps import Loader
from next.deps.cache import _CACHE_MISS, RequestMemo, request_memo
from next.deps.runner import batch_scope
from next.deps.scope import RequestScope
from next.suspense import (
    Addressing,
    Admit,
    BoundaryExecutor,
    BoundarySession,
    BoundaryShedError,
    Capacity,
    Caps,
    ExecutorStoppedError,
    ForegroundFacts,
    Group,
    Job,
    Lane,
    LaneExecutor,
    Outcome,
    Placement,
    Rendered,
    RequestThreadExecutor,
    Rule,
    Server,
    Site,
    Verdict,
    background_loop,
)
from next.suspense.coordinator import (
    COSTS,
    IDLE,
    Admitter,
    Coordinator,
    Emit,
    Foreground,
    Machine,
    ProtocolError,
    Settlement,
    ThreadDriver,
    Thresholds,
    drivers,
    foreground,
    runner,
)
from next.tasks import StealableTask


POOL = Thresholds("pool", 300_000, 0.8, 2_000_000, 3)
ADAPTIVE = Thresholds("adaptive", 300_000, 0.8, 2_000_000, 3)
HANDOFF = Thresholds("adaptive", 300_000, 0.8, 2_000_000, 0)
WSGI = ForegroundFacts(Server.WSGI_THREADED)
ASGI = ForegroundFacts(Server.ASGI)
ATOMIC_WSGI = ForegroundFacts(Server.WSGI_THREADED, atomic=("default",))
ATOMIC_ASGI = ForegroundFacts(Server.ASGI, atomic=("default",))
WAIT = 5.0
RUNNING_TIMEOUT = 0.3

type Body = Callable[[], Rendered[str]]
type AsyncBody = Callable[[], Coroutine[Any, Any, Rendered[str]]]

marker = contextvars.ContextVar[str]("marker", default="unset")


def _fallback(kind: ErrorKind) -> Rendered[str]:
    return Rendered(f"fallback:{kind}", "")


def _session(
    *,
    facts: ForegroundFacts | None = WSGI,
    deadline: float = 5.0,
    grace: float = 1.0,
    per_site: int = 8,
    per_request: int = 4,
) -> BoundarySession[str]:
    session: BoundarySession[str] = BoundarySession.open(
        Caps(deadline, grace, 16, per_site, 3, 64, per_request)
    )
    session.facts = facts
    session.snapshot = contextvars.copy_context()
    return session


def _site(name: str) -> Site:
    return Site(Addressing.COMPONENT, name, DeferMode.STREAM, "list.djx")


def _queue(
    session: BoundarySession[str],
    body: Body | None = None,
    *,
    abody: AsyncBody | None = None,
    site: Site | None = None,
    timeout: float | None = None,
    parallel: bool | None = None,
) -> Job[str]:
    job_id = session.next_id()
    options = BoundaryOptions(timeout=timeout, parallel=parallel)
    job = Job(
        job_id,
        "k",
        site or _site(job_id),
        options,
        session.deadline_for(options, time.monotonic()),
        _fallback,
        run=body,
        arun=abody,
    )
    session.queue(job)
    return job


def _ready(text: str) -> Body:
    def body() -> Rendered[str]:
        return Rendered(text, "")

    return body


def _sleeping(seconds: float) -> Body:
    def body() -> Rendered[str]:
        time.sleep(seconds)
        return Rendered(str(threading.get_ident()), "")

    return body


def _ident() -> Rendered[str]:
    return Rendered(str(threading.get_ident()), "")


def _meeting(barrier: threading.Barrier) -> Body:
    """Return a body that passes only once every party is in flight with it."""

    def body() -> Rendered[str]:
        barrier.wait()
        return Rendered(str(threading.get_ident()), "")

    return body


def _gated(started: threading.Event, release: threading.Event) -> Body:
    def body() -> Rendered[str]:
        started.set()
        release.wait(WAIT)
        return Rendered("late", "")

    return body


def _recorded(log: list[object]) -> Callable[[Sequence[int]], dict[int, int]]:
    def fetch(keys: Sequence[int]) -> dict[int, int]:
        log.append(sorted(keys))
        return {key: key * 10 for key in keys}

    return fetch


def _loading(
    loader: Loader[int, int], key: int, barrier: threading.Barrier | None = None
) -> Body:
    def body() -> Rendered[str]:
        if barrier is not None:
            barrier.wait()
        return Rendered(str(loader.load_sync(key)), "")

    return body


def _scoped(session: BoundarySession[str]) -> RequestScope:
    """Give the jobs of `session` a request scope on the background loop."""
    scope = RequestScope(loop=background_loop.get())
    session.scope = scope
    assert session.snapshot is not None
    session.snapshot.run(batch_scope.set, scope)
    return scope


def _close(scope: RequestScope) -> None:
    loop = background_loop.get()
    asyncio.run_coroutine_threadsafe(scope.close(), loop).result(WAIT)


async def _hop() -> Rendered[str]:
    return Rendered(str(await sync_to_async(threading.get_ident)()), "")


async def _forever() -> Rendered[str]:
    await asyncio.sleep(60)
    return Rendered("never", "")


def _html(item: Outcome[str] | Settlement[str]) -> str:
    assert isinstance(item, Outcome)
    return item.rendered.html


def _ids(batches: list[Emit[str]]) -> list[list[str]]:
    return [[item.id for item in batch.items] for batch in batches]


def _items(batches: list[Emit[str]]) -> dict[str, Outcome[str] | Settlement[str]]:
    return {item.id: item for batch in batches for item in batch.items}


class _Admission:
    def __init__(self, *verdicts: Admit) -> None:
        self.verdicts = list(verdicts) or [Admit.POOL]

    def admit(self, capacity: Capacity, *, forced: bool) -> Admit:
        if len(self.verdicts) > 1:
            return self.verdicts.pop(0)
        return self.verdicts[0]


class _Failing:
    def admit(self, capacity: Capacity, *, forced: bool) -> Admit:
        msg = "admission broke"
        raise RuntimeError(msg)


class _Counting(LaneExecutor):
    """A pool that counts the units submitted to it."""

    def __init__(self, workers: int) -> None:
        super().__init__({"OPTIONS": {"MAX_WORKERS": workers, "CONN_IDLE": 60.0}})
        self.submitted = 0

    def submit[R](self, task: StealableTask[R]) -> Future[R]:
        self.submitted += 1
        return super().submit(task)


class _Held(BoundaryExecutor):
    """A pool whose busy workers never start a unit, so the test decides what runs."""

    def __init__(self, *, stealable: bool = True) -> None:
        self.stealable = stealable
        self.queued: list[StealableTask[Any]] = []
        self.abandoned: list[StealableTask[Any]] = []

    def submit[R](self, task: StealableTask[R]) -> Future[R]:
        self.queued.append(task)
        return task.future

    def steal(self, task: StealableTask[Any]) -> bool:
        if not self.stealable or task not in self.queued:
            return False
        self.queued.remove(task)
        return task.run()

    def capacity(self) -> Capacity:
        return Capacity(4, 4, len(self.queued), 0.0)

    def shutdown(self, timeout: float = 5.0) -> None:
        self.queued.clear()

    def abandon(self, task: StealableTask[Any]) -> bool:
        self.abandoned.append(task)
        return True

    def start(self, index: int) -> StealableTask[Any]:
        """Take a queued unit as a worker would, without running it."""
        task = self.queued.pop(index)
        assert task.claim()
        return task


class _Idle(_Held):
    """A pool reporting free workers, which start a unit only when the test runs it."""

    def __init__(self, units: int) -> None:
        super().__init__()
        self.units = units
        self.submitted = threading.Event()

    def submit[R](self, task: StealableTask[R]) -> Future[R]:
        future = super().submit(task)
        if len(self.queued) == self.units:
            self.submitted.set()
        return future

    def capacity(self) -> Capacity:
        return Capacity(4, 0, len(self.queued), 0.0)


class _Started(_Held):
    """A pool whose one worker claims the first unit before its submission returns."""

    def __init__(self) -> None:
        super().__init__()
        self.worker: threading.Thread | None = None

    def submit[R](self, task: StealableTask[R]) -> Future[R]:
        if self.worker is not None:
            return super().submit(task)
        claimed = threading.Event()
        task.when_claimed(claimed.set)
        self.worker = threading.Thread(target=task.run)
        self.worker.start()
        assert claimed.wait(WAIT)
        return task.future

    def join(self) -> None:
        if self.worker is not None:
            self.worker.join(WAIT)


class _Full(_Held):
    """A pool whose queue is always full, counting the units it refused."""

    def __init__(self) -> None:
        super().__init__()
        self.refused = 0

    def submit[R](self, task: StealableTask[R]) -> Future[R]:
        self.refused += 1
        raise BoundaryShedError


@contextlib.contextmanager
def _queue_timeout(seconds: float) -> Iterator[None]:
    options = {"EXECUTOR": {"OPTIONS": {"QUEUE_TIMEOUT": seconds}}}
    with override_settings(NEXT_FRAMEWORK={"DEFER": options}):
        yield


class _Rejecting(_Held):
    def submit[R](self, task: StealableTask[R]) -> Future[R]:
        task.reject(ExecutorStoppedError())
        return task.future


class _Stopped(_Held):
    def submit[R](self, task: StealableTask[R]) -> Future[R]:
        raise ExecutorStoppedError


class _Lingering(Foreground):
    """Permits whose wait outlasts the deadline it is given."""

    async def wait(self, until: float) -> bool:
        return await super().wait(until + WAIT)


class _Expiring(Foreground):
    """Permits whose wait gives up at once, as if its deadline had passed."""

    async def wait(self, until: float) -> bool:
        return await super().wait(time.monotonic())


class _Refusals(Foreground):
    """Permits counting the takes they refuse."""

    def __init__(self, limit: int) -> None:
        super().__init__(limit)
        self.refused = 0

    def take(self) -> bool:
        taken = super().take()
        self.refused += not taken
        return taken


@pytest.fixture()
def pools() -> Iterator[Callable[[int], _Counting]]:
    """Build pools of a given size and stop every one after the test."""
    made: list[_Counting] = []

    def make(workers: int = 16) -> _Counting:
        executor = _Counting(workers)
        made.append(executor)
        return executor

    yield make
    for executor in made:
        executor.shutdown(5)


def _coordinator(
    executor: BoundaryExecutor,
    *,
    admission: Admitter | None = None,
    thresholds: Thresholds = POOL,
    permits: Foreground | None = None,
) -> Coordinator[str]:
    return Coordinator(
        executor=executor,
        admission=admission or _Admission(),
        thresholds=thresholds,
        permits=permits or Foreground(4),
    )


async def _until_loop(holds: Callable[[], bool]) -> None:
    """Yield to the loop until `holds`, failing after a bounded number of turns."""
    for _ in range(5000):
        if holds():
            return
        await asyncio.sleep(0.001)
    pytest.fail("the condition never held")


async def _left_tasks() -> list[asyncio.Task[Any]]:
    """Return the other tasks of the running loop that do not end within `WAIT`."""
    current = asyncio.current_task()
    others = {task for task in asyncio.all_tasks() if task is not current}
    if not others:
        return []
    _, pending = await asyncio.wait(others, timeout=WAIT)
    return list(pending)


def _loop_tasks() -> list[asyncio.Task[Any]]:
    """Return the tasks of the background loop that do not end within `WAIT`."""
    loop = background_loop.current()
    if loop is None:
        return []
    return asyncio.run_coroutine_threadsafe(_left_tasks(), loop).result(2 * WAIT)


class TestThreadDriver:
    """Tests for ``Coordinator.run`` on the request thread."""

    def test_three_jobs_run_in_parallel_on_the_pool(self, pools) -> None:
        session = _session()
        barrier = threading.Barrier(3, timeout=WAIT)
        jobs = [_queue(session, _meeting(barrier)) for _ in range(3)]
        executor = pools()
        batches = list(_coordinator(executor).run(session))
        assert executor.submitted == 3
        assert sorted(_items(batches)) == sorted(job.id for job in jobs)
        assert all(isinstance(item, Outcome) for item in _items(batches).values())
        assert len({_html(item) for item in _items(batches).values()}) == 3

    def test_a_lone_job_runs_on_the_request_thread_without_a_submission(
        self, pools
    ) -> None:
        session = _session()
        job = _queue(session, _ident)
        executor = pools()
        coordinator = _coordinator(executor, thresholds=ADAPTIVE)
        (batch,) = list(coordinator.run(session))
        assert _html(batch.items[0]) == str(threading.get_ident())
        assert executor.submitted == 0
        assert coordinator.timings[job.id].lane is Lane.REQUEST
        assert coordinator.timings[job.id].placement == Placement(Verdict.ALONE)

    def test_a_rule_runs_every_job_on_the_request_thread_in_document_order(
        self, pools
    ) -> None:
        session = _session(facts=ATOMIC_WSGI)
        order: list[tuple[str, int]] = []

        def body(name: str) -> Body:
            def run() -> Rendered[str]:
                order.append((name, threading.get_ident()))
                return Rendered(name, "")

            return run

        for name in "abc":
            _queue(session, body(name))
        executor = pools()
        coordinator = _coordinator(executor)
        list(coordinator.run(session))
        assert order == [(name, threading.get_ident()) for name in "abc"]
        assert executor.submitted == 0
        assert {timing.placement for timing in coordinator.timings.values()} == {
            Placement(Rule.ATOMIC)
        }

    def test_an_async_hop_lands_on_the_request_thread(self, pools) -> None:
        session = _session()
        job = _queue(session, abody=_hop)
        (batch,) = list(_coordinator(pools()).run(session))
        assert _html(batch.items[0]) == str(threading.get_ident())
        assert batch.items[0].timing.lane is Lane.LOOP
        assert job.id == batch.items[0].id

    def test_a_buffered_run_steals_from_a_saturated_pool(self) -> None:
        session = _session()
        for _ in range(3):
            _queue(session, _ident)
        executor = _Started()
        coordinator = _coordinator(executor)
        try:
            batches = list(coordinator.run(session, buffered=True))
        finally:
            executor.join()
        assert executor.queued == []
        idents = {_html(item) for item in _items(batches).values()}
        assert str(threading.get_ident()) in idents
        lanes = {timing.lane for timing in coordinator.timings.values()}
        assert lanes == {Lane.POOL, Lane.REQUEST}

    def test_a_stream_steals_nothing_while_its_own_unit_runs(self) -> None:
        log: list[str] = []

        def running() -> Rendered[str]:
            log.append("running")
            time.sleep(0.05)
            log.append("ran")
            return Rendered("running", "")

        def queued() -> Rendered[str]:
            log.append("stolen")
            return Rendered("queued", "")

        session = _session()
        _queue(session, running)
        _queue(session, queued)
        executor = _Started()
        try:
            list(_coordinator(executor).run(session))
        finally:
            executor.join()
        assert log == ["running", "ran", "stolen"]

    def test_a_stream_steals_nothing_while_its_async_unit_runs(self) -> None:
        settled = threading.Event()
        seen: list[bool] = []

        async def slow() -> Rendered[str]:
            await asyncio.sleep(0.05)
            settled.set()
            return Rendered("slow", "")

        def pooled() -> Rendered[str]:
            seen.append(settled.is_set())
            return Rendered("pooled", "")

        session = _session()
        _queue(session, abody=slow)
        _queue(session, pooled)
        list(_coordinator(_Held()).run(session))
        assert seen == [True]

    def test_a_stream_leaves_queued_units_to_free_workers(self) -> None:
        release = threading.Event()

        def slow() -> Rendered[str]:
            release.wait(WAIT)
            return Rendered("slow", "")

        session = _session()
        slow_job = _queue(session, slow)
        fast_job = _queue(session, _ready("fast"))
        executor = _Idle(2)

        def worker() -> None:
            executor.submitted.wait(WAIT)
            slowest, fast = executor.queued
            fast.run()
            release.wait(WAIT)
            slowest.run()

        thread = threading.Thread(target=worker)
        thread.start()
        stream = _coordinator(executor).run(session)
        try:
            assert _ids([next(stream)]) == [[fast_job.id]]
            release.set()
            assert _ids(list(stream)) == [[slow_job.id]]
        finally:
            release.set()
            thread.join(WAIT)

    def test_a_timed_out_job_settles_through_its_fallback(self, pools) -> None:
        started = threading.Event()
        release = threading.Event()
        done = threading.Event()
        gated = _gated(started, release)

        def slow() -> Rendered[str]:
            rendered = gated()
            done.set()
            return rendered

        session = _session()
        job = _queue(session, slow, timeout=RUNNING_TIMEOUT)
        coordinator = _coordinator(pools())
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
        session = _session()
        _queue(session, _ready("a"))
        _queue(session, _ready("b"))
        coordinator = _coordinator(pools(), admission=_Admission(Admit.SHED))
        items = _items(list(coordinator.run(session)))
        assert {item.kind for item in items.values()} == {ErrorKind.OVERLOADED}
        assert coordinator.totals is not None
        assert coordinator.totals.shed == 2

    def test_a_group_reports_each_member_in_one_batch(self, pools) -> None:
        session = _session(per_site=1)
        site = _site("row")
        jobs = [_queue(session, _sleeping(0.01), site=site) for _ in range(4)]
        assert [type(unit) for unit in session.plan().units] == [Job, Group]
        coordinator = _coordinator(pools())
        batches = list(coordinator.run(session))
        assert [jobs[1].id, jobs[2].id, jobs[3].id] in _ids(batches)
        members = [_html(_items(batches)[job.id]) for job in jobs[1:]]
        assert len(set(members)) == 1
        assert set(coordinator.timings) == {job.id for job in jobs}

    def test_a_group_member_past_its_deadline_never_starts(self, pools) -> None:
        ran: list[str] = []

        def late() -> Rendered[str]:
            ran.append("late")
            return Rendered("late", "")

        session = _session(per_site=1)
        site = _site("row")
        _queue(session, _ready("first"), site=site)
        _queue(session, _sleeping(0.15), site=site)
        skipped = _queue(session, late, site=site, timeout=0.05)
        items = _items(list(_coordinator(pools()).run(session)))
        assert items[skipped.id].kind is ErrorKind.TIMEOUT
        assert ran == []

    def test_parents_are_written_before_the_jobs_they_nest(self, pools) -> None:
        session = _session()
        parent_id = session.next_id()
        child = Job(
            session.next_id(),
            "k",
            _site("child"),
            BoundaryOptions(),
            session.deadline,
            _fallback,
            run=_ready("child"),
            parent=parent_id,
            depth=1,
        )

        def parent() -> Rendered[str]:
            return Rendered("parent", "", nested=(child,))

        session.queue(
            Job(
                parent_id,
                "k",
                _site("parent"),
                BoundaryOptions(),
                session.deadline,
                _fallback,
                run=parent,
            )
        )
        batches = list(_coordinator(pools()).run(session))
        assert _ids(batches) == [[parent_id], [child.id]]

    def test_a_quiet_wait_yields_idle_batches_that_acknowledge_nothing(
        self, pools
    ) -> None:
        started = threading.Event()
        release = threading.Event()
        session = _session()
        fast = _queue(session, _ready("fast"))
        held = _queue(session, _gated(started, release))
        coordinator = _coordinator(pools())
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
        assert _ids(emits) == [[fast.id], [held.id]]
        assert [call.args[1] for call in written.call_args_list] == [fast.id, held.id]
        assert (1, 1) in quiet
        assert all(written_before == seen for seen, written_before in quiet)
        assert IDLE.items == ()

    def test_without_idle_no_idle_batch_is_yielded(self, pools) -> None:
        session = _session()
        _queue(session, _sleeping(0.05))
        batches = list(_coordinator(pools()).run(session))
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

        session = _session()
        first = _queue(session, body("first"), parallel=False)
        _queue(session, body("queued"))
        _queue(session, body("running"))
        executor = _Held()
        coordinator = _coordinator(executor)
        stream = coordinator.run(session)
        assert _ids([next(stream)]) == [[first.id]]
        running = executor.start(1)
        closing = threading.Thread(target=stream.close)
        closing.start()
        closing.join(WAIT)
        assert not closing.is_alive()
        assert ran == ["first"]
        assert executor.queued == []
        assert executor.abandoned == [running]
        assert coordinator.finish is not None
        assert coordinator.finish.aborted
        assert coordinator.finish.totals.abandoned == 2

    def test_closing_mid_stream_cancels_loop_tasks(self, pools) -> None:
        session = _session()
        _queue(session, _ready("fast"), parallel=False)
        _queue(session, abody=_forever)
        coordinator = _coordinator(pools())
        stream = coordinator.run(session)
        next(stream)
        stream.close()
        assert _loop_tasks() == []
        assert coordinator.finish is not None
        assert coordinator.finish.totals.cancelled == 1

    def test_a_timed_out_async_job_is_cancelled(self, pools) -> None:
        session = _session()
        job = _queue(session, abody=_forever, timeout=0.05)
        _queue(session, _sleeping(0.15))
        items = _items(list(_coordinator(pools()).run(session)))
        assert items[job.id].kind is ErrorKind.TIMEOUT
        assert _loop_tasks() == []

    def test_a_job_raising_system_exit_off_the_request_thread_fails_alone(
        self, pools
    ) -> None:
        def leave() -> Rendered[str]:
            raise SystemExit(3)

        async def aleave() -> Rendered[str]:
            raise SystemExit(4)

        executor = pools()
        session = _session()
        sync_job = _queue(session, leave)
        async_job = _queue(session, abody=aleave)
        items = _items(list(_coordinator(executor).run(session)))
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
        again = _session()
        _queue(again, _ready("after"))
        _queue(again, abody=_hop)
        items = _items(list(_coordinator(executor).run(again)))
        assert all(isinstance(item, Outcome) for item in items.values())

    @pytest.mark.parametrize("interrupt", [SystemExit(3), KeyboardInterrupt()])
    def test_an_interrupt_on_the_request_thread_ends_the_run(
        self, interrupt: BaseException
    ) -> None:
        def leave() -> Rendered[str]:
            raise interrupt

        session = _session()
        _queue(session, leave)
        _queue(session, _ready("held"))
        coordinator = _coordinator(_Held())
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

        session = _session()
        jobs = [_queue(session, fail), _queue(session, abody=afail)]
        items = _items(list(_coordinator(pools()).run(session)))
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

        session = _session(per_site=1)
        site = _site("async-row")
        _queue(session, abody=first, site=site)
        _queue(session, abody=slow, site=site)
        skipped = _queue(session, abody=late, site=site, timeout=0.05)
        items = _items(list(_coordinator(pools()).run(session)))
        assert items[skipped.id].kind is ErrorKind.TIMEOUT
        assert ran == []

    def test_an_async_job_cancelling_itself_fails(self, pools) -> None:
        async def cancel() -> Rendered[str]:
            raise asyncio.CancelledError

        session = _session()
        job = _queue(session, abody=cancel)
        item = _items(list(_coordinator(pools()).run(session)))[job.id]
        assert isinstance(item, Settlement)
        assert isinstance(item.error, asyncio.CancelledError)

    def test_an_unusable_asgiref_fails_async_jobs(
        self, pools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse() -> None:
            msg = "asgiref too old"
            raise ImproperlyConfigured(msg)

        monkeypatch.setattr(drivers, "check_asgiref", refuse)
        session = _session()
        job = _queue(session, abody=_hop)
        item = _items(list(_coordinator(pools()).run(session)))[job.id]
        assert isinstance(item, Settlement)
        assert isinstance(item.error, ImproperlyConfigured)

    def test_jobs_run_in_isolated_contexts(self, pools) -> None:
        def write() -> Rendered[str]:
            marker.set("written")
            return Rendered(marker.get(), "")

        def read() -> Rendered[str]:
            time.sleep(0.05)
            return Rendered(marker.get(), "")

        session = _session()
        writer = _queue(session, write)
        reader = _queue(session, read)
        items = _items(list(_coordinator(pools()).run(session)))
        assert _html(items[writer.id]) == "written"
        assert _html(items[reader.id]) == "unset"
        assert marker.get() == "unset"

    def test_remaining_reads_the_deadline_of_the_job(self, pools) -> None:
        read: list[float] = []

        def left() -> Rendered[str]:
            before = time.monotonic()
            read.append(remaining() or 0.0)
            read.extend((before, time.monotonic()))
            return Rendered("read", "")

        session = _session(deadline=60.0)
        job = _queue(session, left, timeout=30.0)
        list(_coordinator(pools()).run(session))
        assert job.deadline < session.deadline
        value, before, after = read
        assert job.deadline - after <= value <= job.deadline - before

    def test_a_run_feeds_the_cost_table(self, pools) -> None:
        session = _session()
        site = _site(f"costed-{session.seal}")
        _queue(session, _sleeping(0.01), site=site)
        list(_coordinator(pools()).run(session))
        cost = COSTS.cost(site.cost_key)
        assert cost is not None
        assert cost.runs == 1
        assert cost.wall >= 10_000_000

    def test_a_full_queue_hands_its_units_to_the_request_thread(self) -> None:
        session = _session()
        jobs = [_queue(session, _ident) for _ in range(2)]
        executor = _Full()
        coordinator = _coordinator(executor)
        items = _items(list(coordinator.run(session)))
        assert [_html(items[job.id]) for job in jobs] == [
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

        session = _session()
        site = _site(f"served-{session.seal}")
        _queue(session, burn, site=site)
        list(_coordinator(pools(), thresholds=ADAPTIVE).run(session))
        cost = COSTS.cost(site.cost_key)
        assert cost is not None
        assert cost.cpu == 0.0

    def test_a_unit_the_pool_rejects_fails_its_jobs(self) -> None:
        session = _session()
        job = _queue(session, _ready("never"))
        _queue(session, _ready("never"))
        item = _items(list(_coordinator(_Rejecting()).run(session)))[job.id]
        assert isinstance(item, Settlement)
        assert isinstance(item.error, ExecutorStoppedError)

    def test_a_unit_the_pool_refuses_to_take_fails_its_jobs(self) -> None:
        session = _session()
        job = _queue(session, _ready("never"))
        _queue(session, _ready("never"))
        item = _items(list(_coordinator(_Stopped()).run(session)))[job.id]
        assert isinstance(item, Settlement)
        assert isinstance(item.error, ExecutorStoppedError)

    def test_a_capacity_reading_admits_queued_units_again(self) -> None:
        session = _session(deadline=0.2, per_request=1)
        held = _queue(session, _ready("held"))
        shed = _queue(session, _ready("shed"))
        executor = _Held(stealable=False)
        coordinator = _coordinator(
            executor, admission=_Admission(Admit.POOL, Admit.SHED)
        )
        batches = list(coordinator.run(session))
        assert _ids(batches) == [[shed.id], [held.id]]
        assert _items(batches)[held.id].kind is ErrorKind.TIMEOUT
        assert len(executor.abandoned) == 1

    def test_an_empty_session_finishes_without_a_batch(self, pools) -> None:
        coordinator = _coordinator(pools())
        assert list(coordinator.run(_session())) == []
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
            session = _session(facts=None)
            session.snapshot = None
            _queue(session, read)
            return list(_coordinator(pools(), thresholds=ADAPTIVE).run(session))

        (batch,) = contextvars.copy_context().run(scenario)
        assert _html(batch.items[0]) == "caller"

    def test_the_process_backends_serve_a_bare_coordinator(self) -> None:
        session = _session()
        _queue(session, _ident)
        coordinator: Coordinator[str] = Coordinator()
        (batch,) = list(coordinator.run(session))
        assert _html(batch.items[0]) == str(threading.get_ident())

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

        session = _session()
        session.scope = scope
        jobs = [_queue(session, abody=scoped) for _ in range(2)]
        items = _items(list(_coordinator(pools()).run(session)))
        assert [_html(items[job.id]) for job in jobs] == ["seeded-own"] * 2
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
            session = _session()
            session.scope = scope
            _queue(session, abody=scoped)
            return list(_coordinator(pools()).run(session))

        (batch,) = contextvars.copy_context().run(scenario)
        assert _html(batch.items[0]) == "True"

    @pytest.mark.parametrize("ending", ["returns", "raises"])
    def test_a_batch_closes_only_once_a_job_not_loading_ends(
        self, pools, ending: str
    ) -> None:
        log: list[object] = []
        loader = Loader(_recorded(log), window=3600)
        barrier = threading.Barrier(2, timeout=WAIT)

        def other() -> Rendered[str]:
            barrier.wait()
            time.sleep(0.05)
            log.append("ended")
            if ending == "raises":
                msg = "other failed"
                raise RuntimeError(msg)
            return Rendered("other", "")

        session = _session()
        scope = _scoped(session)
        _queue(session, other)
        loading = _queue(session, _loading(loader, 1, barrier))
        try:
            items = _items(list(_coordinator(pools()).run(session)))
        finally:
            _close(scope)
        assert _html(items[loading.id]) == "10"
        assert log == ["ended", [1]]

    def test_a_job_past_its_deadline_frees_the_batch_it_held_open(self, pools) -> None:
        log: list[object] = []
        loader = Loader(_recorded(log), window=3600)
        barrier = threading.Barrier(2, timeout=WAIT)
        release = threading.Event()

        def blocked() -> Rendered[str]:
            barrier.wait()
            release.wait(WAIT)
            return Rendered("late", "")

        session = _session()
        scope = _scoped(session)
        stuck = _queue(session, blocked, timeout=RUNNING_TIMEOUT)
        loading = _queue(session, _loading(loader, 1, barrier))
        try:
            items = _items(list(_coordinator(pools()).run(session)))
        finally:
            release.set()
            _close(scope)
        assert items[stuck.id].kind is ErrorKind.TIMEOUT
        assert _html(items[loading.id]) == "10"
        assert log == [[1]]

    def test_jobs_the_request_thread_steals_close_their_own_batches(self) -> None:
        log: list[object] = []
        loader = Loader(_recorded(log), window=3600)
        session = _session()
        scope = _scoped(session)
        first = _queue(session, _loading(loader, 1))
        second = _queue(session, _loading(loader, 2))
        try:
            items = _items(list(_coordinator(_Held()).run(session)))
        finally:
            _close(scope)
        assert [_html(items[job.id]) for job in (first, second)] == ["10", "20"]
        assert log == [[1], [2]]

    def test_a_parked_job_of_a_closed_stream_ends_with_its_scope(self, pools) -> None:
        loader = Loader(_recorded([]), window=3600)
        flying = threading.Event()
        barrier = threading.Barrier(2, action=flying.set, timeout=WAIT)
        release = threading.Event()
        ended = threading.Event()

        def parked() -> Rendered[str]:
            try:
                return _loading(loader, 1, barrier)()
            finally:
                ended.set()

        def blocked() -> Rendered[str]:
            barrier.wait()
            release.wait(WAIT)
            return Rendered("late", "")

        session = _session()
        scope = _scoped(session)
        _queue(session, _ready("first"))
        _queue(session, parked)
        _queue(session, blocked)
        stream = _coordinator(pools()).run(session)
        try:
            next(stream)
            assert flying.wait(WAIT)
            stream.close()
            assert not ended.wait(0.05)
            _close(scope)
            assert ended.wait(WAIT)
        finally:
            release.set()

    def test_a_machinery_failure_is_logged_once_and_ends_the_stream(
        self, pools, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = _session()
        _queue(session, _ready("a"))
        _queue(session, _ready("b"))
        coordinator = _coordinator(pools(), admission=_Failing())
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
        session = _session()
        coordinator = _coordinator(pools())
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
        session = _session()
        _queue(session, _ready("first"), parallel=False)
        _queue(session, _ready("held"))
        coordinator = _coordinator(_Held(stealable=False))
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
        session = _session()
        _queue(session, _ready("a"))
        coordinator = _coordinator(pools(), admission=_Failing())
        with caplog.at_level(logging.ERROR, logger="next.suspense"):
            list(coordinator.run(session))
        assert [r.getMessage() for r in caplog.records] == [drivers._FAILED]

    def test_an_error_the_consumer_throws_cancels_and_propagates(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = _session()
        _queue(session, _ready("first"), parallel=False)
        _queue(session, _ready("held"))
        coordinator = _coordinator(_Held(stealable=False))
        stream = coordinator.run(session)
        next(stream)
        with pytest.raises(ValueError, match="consumer broke"):
            stream.throw(ValueError("consumer broke"))
        assert coordinator.finish is not None
        assert coordinator.finish.aborted
        assert not caplog.records

    def test_a_message_already_queued_skips_the_serving_wait(self) -> None:
        driver = ThreadDriver[str](_coordinator(_Held()), _session())
        hops = Mock(spec=RequestThreadExecutor)
        driver._hops = hops
        driver._inbox.put(drivers._TICK)
        driver._serve(time.monotonic() + WAIT)
        hops.serve.assert_not_called()
        assert driver._signal is None

    def test_the_serving_wait_ends_at_the_wake_instant(self) -> None:
        driver = ThreadDriver[str](_coordinator(_Held()), _session())
        driver._hops = RequestThreadExecutor()
        started = time.perf_counter()
        driver._serve(time.monotonic() + 0.01)
        assert time.perf_counter() - started < WAIT

    def test_a_report_wakes_the_serving_wait(self) -> None:
        driver = ThreadDriver[str](_coordinator(_Held()), _session())
        driver._hops = RequestThreadExecutor()
        timer = threading.Timer(0.01, driver._post, (drivers._TICK,))
        timer.start()
        started = time.perf_counter()
        driver._serve(time.monotonic() + 30)
        timer.join(5)
        assert time.perf_counter() - started < 5
        assert not driver._inbox.empty()

    def test_queued_hops_run_before_any_steal(self) -> None:
        driver = ThreadDriver[str](_coordinator(_Held()), _session())
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


def _view(
    facts: ForegroundFacts, *bodies: Body | AsyncBody, deadline: float = 5.0
) -> tuple[BoundarySession[str], int]:
    session = _session(facts=facts, deadline=deadline)
    for body in bodies:
        if inspect.iscoroutinefunction(body):
            _queue(session, abody=body)
        else:
            _queue(session, body)
    return session, threading.get_ident()


async def _consume(
    coordinator: Coordinator[str], facts: ForegroundFacts, *bodies: Body | AsyncBody
) -> tuple[list[Emit[str]], int]:
    async with ThreadSensitiveContext():
        session, view = await sync_to_async(_view, thread_sensitive=True)(
            facts, *bodies
        )
        batches = [batch async for batch in coordinator.arun(session)]
    return batches, view


class TestAsyncDriver:
    """Tests for ``Coordinator.arun`` on a server loop."""

    @pytest.fixture(autouse=True)
    def _queued_units_stay_queued(self) -> Iterator[None]:
        """Keep the view thread from taking over pool units unless a test asks."""
        with _queue_timeout(3600):
            yield

    def test_request_lane_jobs_and_async_hops_land_on_the_view_thread(
        self, pools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        hops: list[object] = []

        def counted(
            func: Callable[..., object], *, thread_sensitive: bool
        ) -> Callable[..., Coroutine[Any, Any, object]]:
            hops.append(func)
            return sync_to_async(func, thread_sensitive=thread_sensitive)

        monkeypatch.setattr(drivers, "sync_to_async", counted)
        executor = pools()
        coordinator = _coordinator(executor)
        batches, view = asyncio.run(
            _consume(coordinator, ATOMIC_ASGI, _ident, _ident, _hop)
        )
        items = _items(batches)
        assert {_html(item) for item in items.values()} == {str(view)}
        assert len(hops) == 1
        assert executor.submitted == 0

    def test_pool_units_run_in_parallel_off_the_loop(self, pools) -> None:
        executor = pools()
        coordinator = _coordinator(executor)
        barrier = threading.Barrier(3, timeout=WAIT)
        bodies = [_meeting(barrier) for _ in range(3)]
        batches, view = asyncio.run(_consume(coordinator, ASGI, *bodies))
        assert executor.submitted == 3
        threads = {_html(item) for item in _items(batches).values()}
        assert len(threads) == 3
        assert str(view) not in threads

    def test_a_chosen_request_unit_without_a_permit_goes_to_the_pool(
        self, pools
    ) -> None:
        permits = Foreground(1)
        assert permits.take()
        coordinator = _coordinator(pools(), thresholds=ADAPTIVE, permits=permits)
        batches, _ = asyncio.run(_consume(coordinator, ASGI, _ident))
        (item,) = _items(batches).values()
        assert item.timing.lane is Lane.POOL
        assert item.timing.placement == Placement(Verdict.FOREGROUND_FULL)

    @pytest.mark.parametrize("permits", [_Lingering(1), _Expiring(1)])
    def test_a_forced_unit_waits_for_a_permit_until_its_deadline(
        self, pools, permits: Foreground
    ) -> None:
        assert permits.take()
        coordinator = _coordinator(pools(), permits=permits)

        async def server() -> list[Emit[str]]:
            async with ThreadSensitiveContext():
                session, _ = await sync_to_async(_view, thread_sensitive=True)(
                    ATOMIC_ASGI, _ident, deadline=0.2
                )
                batches = [batch async for batch in coordinator.arun(session)]
            assert await _left_tasks() == []
            return batches

        (item,) = _items(asyncio.run(server())).values()
        assert item.kind is ErrorKind.TIMEOUT
        assert not permits._waiters

    def test_a_forced_unit_runs_once_a_permit_frees(self, pools) -> None:
        permits = Foreground(1)
        assert permits.take()
        coordinator = _coordinator(pools(), permits=permits)

        async def server() -> tuple[list[Emit[str]], int]:
            consuming = asyncio.ensure_future(
                _consume(coordinator, ATOMIC_ASGI, _ident)
            )
            await _until_loop(lambda: bool(permits._waiters))
            permits.release()
            return await consuming

        batches, view = asyncio.run(server())
        (item,) = _items(batches).values()
        assert _html(item) == str(view)
        assert permits.take()

    def test_a_batch_without_a_permit_sends_chosen_units_to_the_pool(
        self, pools
    ) -> None:
        permits = Foreground(1)
        assert permits.take()
        coordinator = _coordinator(pools(), thresholds=HANDOFF, permits=permits)

        async def server() -> tuple[BoundarySession[str], int, list[Emit[str]]]:
            async with ThreadSensitiveContext():
                session, view = await sync_to_async(_view, thread_sensitive=True)(ASGI)
                chosen = _queue(session, _ident)
                COSTS.observe(chosen.site.cost_key, 1_000, 1_000)
                _queue(session, _ident, parallel=False)
                stream = coordinator.arun(session)
                first = asyncio.ensure_future(anext(stream))
                await _until_loop(lambda: bool(permits._waiters))
                permits.release()
                batches = [await first]
                batches.extend([batch async for batch in stream])
            return session, view, batches

        session, view, batches = asyncio.run(server())
        chosen, forced = session.jobs
        items = _items(batches)
        assert items[chosen.id].timing.placement == Placement(Verdict.FOREGROUND_FULL)
        assert items[chosen.id].timing.lane is Lane.POOL
        assert _html(items[forced.id]) == str(view)

    def test_closing_mid_hop_skips_the_rest_of_the_batch(self, pools) -> None:
        started = threading.Event()
        release = threading.Event()
        ran: list[str] = []

        def first() -> Rendered[str]:
            started.set()
            release.wait(WAIT)
            return Rendered("first", "")

        def second() -> Rendered[str]:
            ran.append("second")
            return Rendered("second", "")

        coordinator = _coordinator(pools())

        async def server() -> list[asyncio.Task[Any]]:
            async with ThreadSensitiveContext():
                session, _ = await sync_to_async(_view, thread_sensitive=True)(
                    ATOMIC_ASGI, first, second
                )
                stream = coordinator.arun(session)
                pending = asyncio.ensure_future(anext(stream))
                assert await asyncio.to_thread(started.wait, WAIT)
                pending.cancel()
                await _until_loop(lambda: coordinator.finish is not None)
                assert not pending.done()
                release.set()
                with contextlib.suppress(asyncio.CancelledError):
                    await pending
            current = asyncio.current_task()
            return [task for task in asyncio.all_tasks() if task is not current]

        assert asyncio.run(server()) == []
        assert ran == []
        assert coordinator.finish is not None
        assert coordinator.finish.aborted

    @pytest.mark.parametrize("ending", ["exhausted", "closed", "cancelled"])
    def test_the_stream_ends_only_once_its_running_request_job_returns(
        self, pools, ending: str
    ) -> None:
        started = threading.Event()
        release = threading.Event()
        coordinator = _coordinator(pools())

        async def server() -> tuple[Emit[str], bool]:
            async with ThreadSensitiveContext():
                session, _ = await sync_to_async(_view, thread_sensitive=True)(
                    ATOMIC_ASGI
                )
                _queue(session, _gated(started, release), timeout=RUNNING_TIMEOUT)
                stream = coordinator.arun(session)
                settled = await anext(stream)
                rest = anext(stream, None) if ending == "exhausted" else stream.aclose()
                waiting = asyncio.ensure_future(rest)
                await _until_loop(lambda: coordinator.finish is not None)
                if ending == "cancelled":
                    waiting.cancel()
                    await asyncio.sleep(0)
                held = not waiting.done()
                release.set()
                with contextlib.suppress(asyncio.CancelledError):
                    await waiting
                cancelled = waiting.cancelled()
            assert cancelled is (ending == "cancelled")
            return settled, held

        settled, held = asyncio.run(server())
        assert started.is_set()
        (item,) = settled.items
        assert item.kind is ErrorKind.TIMEOUT
        assert held

    def test_the_stream_ends_only_once_a_cancelled_jobs_hop_returns(
        self, pools
    ) -> None:
        started = threading.Event()
        release = threading.Event()
        returned: list[bool] = []

        def hop() -> None:
            started.set()
            release.wait(WAIT)
            returned.append(True)

        async def abody() -> Rendered[str]:
            await sync_to_async(hop, thread_sensitive=True)()
            return Rendered("hopped", "")

        coordinator = _coordinator(pools())

        async def server() -> tuple[Emit[str], bool, list[bool]]:
            async with ThreadSensitiveContext():
                session, _ = await sync_to_async(_view, thread_sensitive=True)(ASGI)
                _queue(session, abody=abody, timeout=RUNNING_TIMEOUT)
                stream = coordinator.arun(session)
                settled = await anext(stream)
                closing = asyncio.ensure_future(stream.aclose())
                assert await asyncio.to_thread(started.wait, WAIT)
                await asyncio.sleep(0.05)
                held = not closing.done()
                release.set()
                await asyncio.wait_for(closing, WAIT)
                return settled, held, list(returned)

        settled, held, ended = asyncio.run(server())
        (item,) = settled.items
        assert item.kind is ErrorKind.TIMEOUT
        assert held
        assert ended == [True]

    def test_the_stream_ends_only_once_its_sync_batch_calls_return(self, pools) -> None:
        started = threading.Event()
        release = threading.Event()
        calls: list[list[object]] = []

        def fetch(keys: Sequence[int]) -> dict[int, int]:
            calls.append(list(keys))
            started.set()
            release.wait(WAIT)
            return {key: key for key in keys}

        loader = Loader(fetch, window=3600)

        async def abody() -> Rendered[str]:
            await loader.load(1)
            return Rendered("loaded", "")

        coordinator = _coordinator(pools())

        async def server() -> tuple[Emit[str], bool]:
            scope = RequestScope()
            session = _session(facts=ASGI)
            session.scope = scope
            _queue(session, abody=abody, timeout=RUNNING_TIMEOUT)
            stream = coordinator.arun(session)
            try:
                settled = await anext(stream)
                closing = asyncio.ensure_future(stream.aclose())
                assert await asyncio.to_thread(started.wait, WAIT)
                await asyncio.sleep(0.05)
                held = not closing.done()
                release.set()
                await asyncio.wait_for(closing, WAIT)
            finally:
                await scope.close()
            return settled, held

        settled, held = asyncio.run(server())
        (item,) = settled.items
        assert item.kind is ErrorKind.TIMEOUT
        assert held
        assert calls == [[1]]

    def test_async_jobs_share_a_batch_however_late_one_loads(self, pools) -> None:
        log: list[object] = []
        loader = Loader(_recorded(log), window=3600)

        async def early() -> Rendered[str]:
            return Rendered(str(await loader.load(1)), "")

        async def late() -> Rendered[str]:
            await asyncio.sleep(0.05)
            return Rendered(str(await loader.load(2)), "")

        coordinator = _coordinator(pools())

        async def server() -> list[Emit[str]]:
            scope = RequestScope()
            session = _session(facts=ASGI)
            session.scope = scope
            _queue(session, abody=early)
            _queue(session, abody=late)
            try:
                return [batch async for batch in coordinator.arun(session)]
            finally:
                await scope.close()

        items = _items(asyncio.run(server()))
        assert sorted(_html(item) for item in items.values()) == ["10", "20"]
        assert log == [[1, 2]]

    def test_a_full_queue_runs_units_on_the_view_thread_under_a_permit(self) -> None:
        executor = _Full()
        coordinator = _coordinator(executor)
        batches, view = asyncio.run(_consume(coordinator, ASGI, _ident, _ident))
        assert {_html(item) for item in _items(batches).values()} == {str(view)}
        assert executor.refused == 2

    def test_a_unit_neither_the_queue_nor_a_permit_takes_is_shed_once(self) -> None:
        permits = Foreground(1)
        assert permits.take()
        executor = _Full()
        coordinator = _coordinator(executor, permits=permits)
        batches, _ = asyncio.run(_consume(coordinator, ASGI, _ident, _ident))
        kinds = [item.kind for item in _items(batches).values()]
        assert kinds == [ErrorKind.OVERLOADED, ErrorKind.OVERLOADED]
        assert executor.refused == 4
        assert coordinator.totals is not None
        assert coordinator.totals.shed == 2

    def test_a_unit_queued_past_the_queue_timeout_runs_on_the_view_thread(self) -> None:
        executor = _Held()
        coordinator = _coordinator(executor)
        with _queue_timeout(0.01):
            batches, view = asyncio.run(_consume(coordinator, ASGI, _ident))
        (item,) = _items(batches).values()
        assert _html(item) == str(view)
        assert item.timing.lane is Lane.REQUEST
        assert executor.queued == []

    def test_a_queued_unit_waits_for_a_permit_to_be_taken_over(self) -> None:
        permits = _Refusals(1)
        assert permits.take()
        executor = _Held()
        coordinator = _coordinator(executor, permits=permits)

        async def server() -> tuple[list[Emit[str]], int]:
            async with ThreadSensitiveContext():
                session, view = await sync_to_async(_view, thread_sensitive=True)(
                    ASGI, _ident
                )
                stream = coordinator.arun(session)
                first = asyncio.ensure_future(anext(stream))
                await _until_loop(lambda: permits.refused > 0)
                assert len(executor.queued) == 1
                assert not first.done()
                permits.release()
                batches = [await first]
                batches.extend([batch async for batch in stream])
            return batches, view

        with _queue_timeout(0.01):
            batches, view = asyncio.run(server())
        (item,) = _items(batches).values()
        assert _html(item) == str(view)

    def test_aclose_cancels_loop_tasks_and_drops_queued_units(self) -> None:
        executor = _Held()
        coordinator = _coordinator(executor)

        async def server() -> list[asyncio.Task[Any]]:
            async with ThreadSensitiveContext():
                session, _ = await sync_to_async(_view, thread_sensitive=True)(ASGI)
                _queue(session, _ready("first"), parallel=False)
                _queue(session, abody=_forever)
                _queue(session, _ready("queued"))
                stream = coordinator.arun(session)
                await anext(stream)
                await stream.aclose()
            return await _left_tasks()

        assert asyncio.run(server()) == []
        assert executor.queued == []
        assert coordinator.finish is not None
        assert coordinator.finish.totals.cancelled == 1
        assert coordinator.finish.totals.abandoned == 1

    def test_a_timed_out_async_job_settles_and_its_task_ends(self, pools) -> None:
        coordinator = _coordinator(pools())

        async def server() -> tuple[list[Emit[str]], list[asyncio.Task[Any]]]:
            async with ThreadSensitiveContext():
                session, _ = await sync_to_async(_view, thread_sensitive=True)(ASGI)
                job = _queue(session, abody=_forever, timeout=0.05)
                _queue(session, _sleeping(0.15))
                batches = [batch async for batch in coordinator.arun(session)]
                assert _items(batches)[job.id].kind is ErrorKind.TIMEOUT
            return batches, await _left_tasks()

        batches, left = asyncio.run(server())
        assert len(_items(batches)) == 2
        assert left == []

    def test_a_capacity_reading_sheds_queued_units(self) -> None:
        executor = _Held(stealable=False)
        coordinator = _coordinator(
            executor, admission=_Admission(Admit.POOL, Admit.SHED)
        )

        async def server() -> list[Emit[str]]:
            session = _session(facts=ASGI, deadline=0.2, per_request=1)
            _queue(session, _ready("held"))
            _queue(session, _ready("shed"))
            return [batch async for batch in coordinator.arun(session)]

        batches = asyncio.run(server())
        kinds = [item.kind for batch in batches for item in batch.items]
        assert kinds == [ErrorKind.OVERLOADED, ErrorKind.TIMEOUT]
        assert len(executor.abandoned) == 1

    def test_an_empty_session_finishes_at_once(self, pools) -> None:
        coordinator = _coordinator(pools())

        async def server() -> list[Emit[str]]:
            return [batch async for batch in coordinator.arun(_session(facts=None))]

        assert asyncio.run(server()) == []
        assert coordinator.finish is not None
        assert not coordinator.finish.aborted
        assert coordinator.failed is False

    def test_the_process_permits_serve_a_coordinator_without_them(self, pools) -> None:
        coordinator: Coordinator[str] = Coordinator(
            executor=pools(), admission=_Admission(), thresholds=POOL
        )
        batches, view = asyncio.run(_consume(coordinator, ATOMIC_ASGI, _ident))
        assert _html(batches[0].items[0]) == str(view)

    def test_a_machinery_failure_ends_the_async_stream(
        self, pools, caplog: pytest.LogCaptureFixture
    ) -> None:
        coordinator = _coordinator(pools(), admission=_Failing())

        async def server() -> list[Emit[str]]:
            session = _session(facts=ASGI)
            _queue(session, _ready("a"))
            _queue(session, _ready("b"))
            return [batch async for batch in coordinator.arun(session)]

        with caplog.at_level(logging.ERROR, logger="next.suspense"):
            assert asyncio.run(server()) == []
        assert [r.getMessage() for r in caplog.records] == [drivers._FAILED]
        assert coordinator.finish is not None
        assert coordinator.finish.aborted
        assert coordinator.failed is True

    def test_an_error_the_consumer_throws_propagates(self) -> None:
        coordinator = _coordinator(_Held(stealable=False))

        async def server() -> None:
            session = _session(facts=ASGI)
            _queue(session, _ready("first"), parallel=False)
            _queue(session, _ready("held"))
            stream = coordinator.arun(session)
            await anext(stream)
            await stream.athrow(ValueError("consumer broke"))

        with pytest.raises(ValueError, match="consumer broke"):
            asyncio.run(server())
        assert coordinator.finish is not None
        assert coordinator.finish.aborted


class _Interleaved:
    """A lock that runs `between` once, right after its next release."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.between: Callable[[], object] | None = None

    def __enter__(self) -> bool:
        return self._lock.acquire()

    def __exit__(self, *exc: object) -> None:
        self._lock.release()
        between, self.between = self.between, None
        if between is not None:
            between()


class TestForeground:
    """Tests for ``Foreground`` and the process permits."""

    def test_a_release_beside_a_queueing_waiter_reaches_it(self) -> None:
        permits = Foreground(1)
        assert permits.take()
        lock = _Interleaved()
        permits._lock = lock

        async def scenario() -> bool:
            lock.between = permits.release
            return await permits.wait(time.monotonic() + WAIT)

        assert asyncio.run(scenario())
        assert not permits.take()

    def test_take_spends_the_free_permits(self) -> None:
        permits = Foreground(1)
        assert permits.take()
        assert not permits.take()
        permits.release()
        assert permits.take()

    def test_wait_returns_at_once_with_a_free_permit(self) -> None:
        permits = Foreground(1)
        assert asyncio.run(permits.wait(time.monotonic()))

    def test_wait_gives_up_at_its_deadline(self) -> None:
        permits = Foreground(1)
        assert permits.take()
        assert not asyncio.run(permits.wait(time.monotonic() + 0.02))
        assert not permits._waiters
        permits.release()
        assert permits.take()

    def test_a_released_permit_reaches_the_oldest_waiter(self) -> None:
        permits = Foreground(1)
        assert permits.take()

        async def scenario() -> bool:
            asyncio.get_running_loop().call_later(0.02, permits.release)
            return await permits.wait(time.monotonic() + WAIT)

        assert asyncio.run(scenario())
        assert not permits.take()

    def test_a_cancelled_waiter_leaves_without_a_permit(self) -> None:
        permits = Foreground(1)
        assert permits.take()

        async def scenario() -> None:
            waiting = asyncio.ensure_future(permits.wait(time.monotonic() + WAIT))
            await asyncio.sleep(0.01)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting

        asyncio.run(scenario())
        assert not permits._waiters
        assert not permits.take()

    def test_a_granted_waiter_that_left_hands_its_permit_back(self) -> None:
        permits = Foreground(1)
        assert permits.take()
        loop = asyncio.new_event_loop()
        try:
            granted: asyncio.Future[None] = loop.create_future()
            granted.set_result(None)
            permits._leave((loop, granted))
        finally:
            loop.close()
        assert permits.take()

    def test_a_waiter_on_a_closed_loop_is_skipped(self) -> None:
        permits = Foreground(1)
        assert permits.take()
        loop = asyncio.new_event_loop()
        stale: asyncio.Future[None] = loop.create_future()
        loop.close()
        permits._waiters.append((loop, stale))
        permits.release()
        assert not permits._waiters
        assert permits.take()

    def test_a_grant_to_a_waiter_that_left_frees_the_permit(self) -> None:
        permits = Foreground(1)
        assert permits.take()

        async def scenario() -> None:
            gone: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            gone.cancel()
            permits._grant(gone)

        asyncio.run(scenario())
        assert permits.take()

    def test_the_process_permits_follow_max_foreground(self) -> None:
        options = {"EXECUTOR": {"OPTIONS": {"MAX_FOREGROUND": 3}}}
        with override_settings(NEXT_FRAMEWORK={"DEFER": options}):
            held = foreground()
            assert held.limit == 3
            assert foreground() is held
