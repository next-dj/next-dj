import asyncio
import logging
import re
import threading
import time
import weakref
from collections.abc import Callable, Iterator
from concurrent.futures import Future, InvalidStateError, wait
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from asgiref.sync import AsyncToSync, SyncToAsync
from django.core.exceptions import ImproperlyConfigured, SynchronousOnlyOperation
from django.db import connection, transaction
from django.db.utils import ConnectionHandler
from django.test import override_settings
from pytest_django import DjangoDbBlocker

from next.suspense import (
    BoundaryShedError,
    Capacity,
    ExecutorStoppedError,
    LaneExecutor,
    executors,
)
from next.suspense.executors import (
    BackgroundLoop,
    RequestThreadExecutor,
    _forget_pools,
    _Worker,
    background_loop,
    check_asgiref,
    request_executor,
    retire_thread,
    serve_until,
    served_cpu,
)
from next.tasks import StealableTask
from next.testing import override_next_settings
from tests.support.waits import WAIT, until


@contextmanager
def _expiring(future: Future[Any]) -> Iterator[None]:
    def expire() -> None:
        with suppress(InvalidStateError):
            future.set_exception(TimeoutError("the wait never ended"))

    timer = threading.Timer(WAIT, expire)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()


def _interrupt() -> None:
    raise KeyboardInterrupt


def _fail() -> None:
    msg = "hop failed"
    raise ValueError(msg)


async def _connect() -> None:
    connection.ensure_connection()


def _submit_elsewhere(
    executor: RequestThreadExecutor, fn: Callable[..., object], *args: object
) -> Future[object]:
    hops: list[Future[object]] = []
    thread = threading.Thread(
        target=lambda: hops.append(executor.submit(fn, *args)), daemon=True
    )
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    return hops[0]


class _Bare:
    """Stand-in for an asgiref executor class without the attributes the walk reads."""


@pytest.fixture()
def owner() -> Iterator[BackgroundLoop]:
    """Return a loop owner of its own, stopped when the test ends."""
    held = BackgroundLoop()
    yield held
    held.stop()


class TestBackgroundLoop:
    """Tests for ``BackgroundLoop``."""

    def test_no_thread_exists_before_the_first_get(self, owner) -> None:
        before = set(threading.enumerate())
        assert owner.current() is None
        assert set(threading.enumerate()) <= before
        loop = owner.get()
        started = set(threading.enumerate()) - before
        assert [thread.name for thread in started] == ["next-background-loop"]
        assert all(thread.daemon for thread in started)
        assert owner.get() is loop
        assert owner.current() is loop
        owner.stop()
        assert not any(thread.is_alive() for thread in started)
        assert loop.is_closed()

    def test_the_loop_runs_submitted_coroutines(self, owner) -> None:
        future = asyncio.run_coroutine_threadsafe(asyncio.sleep(0, "done"), owner.get())
        assert future.result(5) == "done"

    def test_call_soon_runs_the_callback_on_the_loop(self, owner) -> None:
        ran = threading.Event()
        loop = owner.call_soon(ran.set)
        assert ran.wait(5)
        assert loop is owner.current()

    @pytest.mark.django_db()
    def test_a_database_connection_on_the_loop_raises(self, owner) -> None:
        future = asyncio.run_coroutine_threadsafe(_connect(), owner.get())
        with pytest.raises(SynchronousOnlyOperation):
            future.result(5)

    def test_stop_without_a_loop_does_nothing(self, owner) -> None:
        owner.stop()
        assert owner.current() is None

    def test_forget_drops_the_loop_as_a_forked_child_must(self, owner) -> None:
        inherited = owner.get()
        running = owner._running
        assert running is not None
        owner.forget()
        assert owner.current() is None
        assert owner.get() is not inherited
        owner.stop()
        running.halt()
        running.thread.join(5)
        assert not running.thread.is_alive()

    def test_get_replaces_a_loop_whose_thread_ended(self, owner) -> None:
        ended = owner.get()
        running = owner._running
        assert running is not None
        running.halt()
        running.thread.join(5)
        assert ended.is_closed()
        fresh = owner.get()
        assert fresh is not ended
        future = asyncio.run_coroutine_threadsafe(asyncio.sleep(0, "done"), fresh)
        assert future.result(5) == "done"

    def test_stop_skips_a_loop_whose_thread_ended(self, owner) -> None:
        owner.get()
        running = owner._running
        assert running is not None
        running.halt()
        running.thread.join(5)
        owner.stop()
        assert owner.current() is None

    def test_a_stray_loop_stop_does_not_end_the_thread(self, owner) -> None:
        loop = owner.get()
        loop.call_soon_threadsafe(loop.stop)
        future = asyncio.run_coroutine_threadsafe(asyncio.sleep(0, "done"), loop)
        assert future.result(5) == "done"
        assert owner.get() is loop

    def test_an_interrupt_raised_by_a_callback_is_logged_and_the_loop_resumes(
        self, owner, caplog: pytest.LogCaptureFixture
    ) -> None:
        loop = owner.get()
        owner.call_soon(_interrupt)
        future = asyncio.run_coroutine_threadsafe(asyncio.sleep(0, "done"), loop)
        assert future.result(5) == "done"
        assert owner.get() is loop
        reports = [r for r in caplog.records if r.name == "next.suspense"]
        assert [r.getMessage() for r in reports] == [
            "A task raised an interrupt on the loop."
        ]
        assert reports[0].exc_info is not None
        assert reports[0].exc_info[0] is KeyboardInterrupt

    def test_stop_cancels_the_tasks_left_and_lets_them_settle(self, owner) -> None:
        started = threading.Event()
        reasons: list[tuple[object, ...]] = []

        async def pending() -> None:
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError as error:
                reasons.append(error.args)
                raise

        future = asyncio.run_coroutine_threadsafe(pending(), owner.get())
        assert started.wait(5)
        owner.stop()
        assert future.cancelled()
        assert reasons == [
            ("The background loop stopped before the coroutine finished.",)
        ]

    def test_debug_turns_on_slow_callback_detection_through_the_package_logger(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with override_settings(DEBUG=True):
            loop = background_loop.get()
        assert loop.get_debug()
        assert loop.slow_callback_duration == pytest.approx(0.1)
        finished = threading.Event()
        background_loop.call_soon(time.sleep, 0.15)
        background_loop.call_soon(finished.set)
        assert finished.wait(5)
        slow = [r for r in caplog.records if "took" in r.getMessage()]
        assert slow
        assert {r.name for r in slow} == {"next.suspense"}
        assert {r.levelno for r in slow} == {logging.WARNING}

    def test_asyncio_records_of_other_threads_stay_with_asyncio(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        background_loop.get()
        logging.getLogger("asyncio").warning("elsewhere")
        assert [r.name for r in caplog.records if r.getMessage() == "elsewhere"] == [
            "asyncio"
        ]


class TestCheckAsgiref:
    """Tests for ``check_asgiref``."""

    def test_the_installed_asgiref_passes(self) -> None:
        check_asgiref()

    @pytest.mark.parametrize(
        ("holder", "name"), [(AsyncToSync, "executors"), (SyncToAsync, "threadlocal")]
    )
    def test_a_missing_attribute_is_named(
        self, monkeypatch: pytest.MonkeyPatch, holder: type, name: str
    ) -> None:
        monkeypatch.delattr(holder, name)
        with pytest.raises(
            ImproperlyConfigured, match=rf"lacks {holder.__name__}\.{name},"
        ):
            check_asgiref()

    def test_the_message_names_the_minimum_version(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delattr(AsyncToSync, "executors")
        with pytest.raises(
            ImproperlyConfigured, match=r"Install asgiref 3\.9 or later\.$"
        ):
            check_asgiref()

    def test_attributes_of_another_kind_count_as_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(AsyncToSync, "executors", object())
        monkeypatch.setattr(SyncToAsync, "threadlocal", {})
        expected = "lacks AsyncToSync.executors, SyncToAsync.threadlocal,"
        with pytest.raises(ImproperlyConfigured, match=re.escape(expected)):
            check_asgiref()

    def test_an_executor_without_the_walked_attributes_is_named(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(executors, "CurrentThreadExecutor", _Bare)
        expected = (
            "lacks CurrentThreadExecutor._work_ready, CurrentThreadExecutor._broken, "
            "CurrentThreadExecutor._old_executor,"
        )
        with pytest.raises(ImproperlyConfigured, match=re.escape(expected)):
            check_asgiref()

    def test_a_pass_is_kept_for_the_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        check_asgiref()
        monkeypatch.setattr(AsyncToSync, "executors", object())
        check_asgiref()

    def test_a_failure_is_kept_for_the_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(AsyncToSync, "executors", object())
        with pytest.raises(ImproperlyConfigured):
            check_asgiref()
        monkeypatch.undo()
        with pytest.raises(ImproperlyConfigured, match=r"AsyncToSync\.executors"):
            check_asgiref()


class TestRequestThreadExecutor:
    """Tests for ``RequestThreadExecutor``."""

    def test_a_hop_runs_on_the_serving_thread(self) -> None:
        executor = RequestThreadExecutor()
        done: Future[None] = Future()
        submitted: list[Future[int]] = []

        def submit() -> None:
            hop = executor.submit(threading.get_ident)
            hop.add_done_callback(lambda _: done.set_result(None))
            submitted.append(hop)

        thread = threading.Thread(target=submit, daemon=True)
        thread.start()
        executor.serve(done, WAIT)
        thread.join(5)
        assert not thread.is_alive()
        assert submitted[0].result(0) == threading.get_ident()

    def test_a_hop_from_its_own_thread_raises(self) -> None:
        executor = RequestThreadExecutor()
        with pytest.raises(
            RuntimeError,
            match="cannot submit onto RequestThreadExecutor from its own thread",
        ):
            executor.submit(threading.get_ident)

    def test_a_wake_makes_the_server_recheck_its_future(self) -> None:
        executor = RequestThreadExecutor()
        future = MagicMock(spec=Future)
        future.done.side_effect = [False, True]
        server = threading.Thread(target=executor.serve, args=(future,), daemon=True)
        server.start()
        executor.wake()
        server.join(5)
        assert not server.is_alive()

    def test_a_zero_timeout_runs_the_queued_hops_and_returns(self) -> None:
        executor = RequestThreadExecutor()
        first = _submit_elsewhere(executor, threading.get_ident)
        second = _submit_elsewhere(executor, threading.get_ident)
        executor.serve(Future(), 0.0)
        assert first.result(0) == second.result(0) == threading.get_ident()

    def test_a_timed_serve_returns_when_no_hop_arrives(self) -> None:
        executor = RequestThreadExecutor()
        future: Future[None] = Future()
        server = threading.Thread(target=executor.serve, args=(future, 0.01))
        server.start()
        server.join(WAIT)
        assert not server.is_alive()
        assert not future.done()

    def test_serve_returns_at_once_for_a_done_future(self) -> None:
        executor = RequestThreadExecutor()
        done: Future[None] = Future()
        done.set_result(None)
        hop = _submit_elsewhere(executor, threading.get_ident)
        executor.serve(done, WAIT)
        assert not hop.done()

    def test_a_hop_taken_after_the_future_is_done_is_left_for_close(self) -> None:
        executor = RequestThreadExecutor()
        calls: list[int] = []
        hop = _submit_elsewhere(executor, calls.append, 1)
        future = MagicMock(spec=Future)
        future.done.side_effect = [False, True]
        executor.serve(future, WAIT)
        assert not hop.done()
        executor.close()
        with pytest.raises(RuntimeError, match="no longer serves"):
            hop.result(0)
        assert calls == []

    def test_an_interrupt_in_a_hop_leaves_serve(self) -> None:
        executor = RequestThreadExecutor()
        hop = _submit_elsewhere(executor, _interrupt)
        with pytest.raises(KeyboardInterrupt):
            executor.serve(Future(), WAIT)
        error = hop.exception(0)
        assert isinstance(error, RuntimeError)
        assert isinstance(error.__cause__, KeyboardInterrupt)

    def test_an_error_in_a_hop_settles_it_and_serving_goes_on(self) -> None:
        executor = RequestThreadExecutor()
        failed = _submit_elsewhere(executor, _fail)
        done: Future[None] = Future()
        _submit_elsewhere(executor, done.set_result, None)
        executor.serve(done, WAIT)
        assert isinstance(failed.exception(0), ValueError)

    def test_close_rejects_the_hops_still_queued(self) -> None:
        executor = RequestThreadExecutor()
        calls: list[int] = []
        hop = _submit_elsewhere(executor, calls.append, 1)
        executor.wake()
        executor.close()
        with pytest.raises(RuntimeError, match="no longer serves"):
            hop.result(0)
        assert calls == []

    def test_a_closed_executor_rejects_new_hops(self) -> None:
        executor = RequestThreadExecutor()
        executor.close()
        hop = _submit_elsewhere(executor, threading.get_ident)
        with pytest.raises(RuntimeError, match="no longer serves"):
            hop.result(0)

    def test_the_loop_markers_a_hop_sets_are_put_back(self) -> None:
        executor = RequestThreadExecutor()
        markers = SyncToAsync.threadlocal
        before = dict(vars(markers))
        done: Future[None] = Future()

        def mark() -> None:
            markers.main_event_loop = object()
            markers.task_context = []
            done.set_result(None)

        _submit_elsewhere(executor, mark)
        executor.serve(done, WAIT)
        assert dict(vars(markers)) == before

    def test_the_cpu_of_a_served_hop_is_counted_for_its_thread(self) -> None:
        executor = RequestThreadExecutor()
        before = served_cpu()

        def burn() -> None:
            started = time.thread_time_ns()
            while time.thread_time_ns() == started:
                pass

        hop = _submit_elsewhere(executor, burn)
        executor.serve(hop, WAIT)
        assert served_cpu() > before

    def test_it_reads_as_a_quit_executor_to_asgiref(self) -> None:
        executor = RequestThreadExecutor()
        with executor._work_ready:
            assert executor._broken
        assert executor._old_executor is None


class TestRequestExecutor:
    """Tests for ``request_executor``."""

    def test_a_nested_caller_shares_the_outer_executor(self) -> None:
        with request_executor() as outer:
            with request_executor() as inner:
                assert inner is outer
            assert _submit_elsewhere(outer, threading.get_ident).done() is False
        with request_executor() as fresh:
            assert fresh is not outer
        with pytest.raises(RuntimeError, match="no longer serves"):
            _submit_elsewhere(outer, threading.get_ident).result(0)

    def test_a_close_from_another_thread_leaves_the_owners_local_alone(self) -> None:
        held = request_executor()
        stale = held.__enter__()
        errors: list[BaseException] = []

        def close() -> None:
            try:
                held.__exit__(None, None, None)
            except AttributeError as error:
                errors.append(error)

        closer = threading.Thread(target=close, daemon=True)
        closer.start()
        closer.join(5)
        assert errors == []
        assert stale.closed is True
        future: Future[int] = Future()
        future.set_result(1)
        serve_until(future)
        with request_executor() as fresh:
            assert fresh is not stale
        with request_executor() as later:
            assert later is not fresh


class TestServeUntil:
    """Tests for ``serve_until``."""

    def test_a_thread_no_driver_serves_waits_plainly(self) -> None:
        future: Future[int] = Future()
        timer = threading.Timer(0.01, future.set_result, (7,))
        timer.start()
        with _expiring(future):
            serve_until(future)
        timer.join(5)
        assert future.result(0) == 7

    def test_a_served_thread_runs_its_hops_while_it_waits(self) -> None:
        future: Future[int] = Future()
        with request_executor() as executor:
            _submit_elsewhere(
                executor, lambda: future.set_result(threading.get_ident())
            )
            with _expiring(future):
                serve_until(future)
        assert future.result(0) == threading.get_ident()

    def test_a_wait_inside_a_hop_serves_the_hops_queued_behind_it(self) -> None:
        inner: Future[int] = Future()
        outer: Future[int] = Future()

        def waiting_hop() -> None:
            _submit_elsewhere(executor, lambda: inner.set_result(1))
            with _expiring(inner):
                serve_until(inner)
            outer.set_result(inner.result(0) + 1)

        with request_executor() as executor:
            _submit_elsewhere(executor, waiting_hop)
            with _expiring(outer):
                serve_until(outer)
        assert outer.result(0) == 2


def _pool_threads() -> set[threading.Thread]:
    return {
        thread
        for thread in threading.enumerate()
        if thread.name.startswith("next-pool-")
    }


def _blocked(gate: threading.Event) -> StealableTask[bool]:
    return StealableTask(lambda: gate.wait(5))


class _Clock:
    """Stand-in for the `time` module of the pool, its monotonic clock moved by hand."""

    def __init__(self) -> None:
        self.now = 100.0

    def monotonic(self) -> float:
        return self.now


class _Connection:
    """Stand-in for a Django connection, with the fields the pool hooks read."""

    def __init__(self, alias: str = "default") -> None:
        self.alias = alias
        self.connection: object | None = object()
        self.in_atomic_block = False
        self.refreshed = 0
        self.closed = threading.Event()
        self.execute_wrappers: list[Callable[..., object]] = []

    def close_if_unusable_or_obsolete(self) -> None:
        self.refreshed += 1

    def close(self) -> None:
        self.connection = None
        self.closed.set()


class _Connections:
    """Stand-in for `django.db.connections`, holding connections per thread."""

    def __init__(self) -> None:
        self._held = threading.local()

    def hold(self, *held: _Connection) -> None:
        self._held.connections = list(held)

    def all(self, *, initialized_only: bool = False) -> list[_Connection]:
        return getattr(self._held, "connections", [])


@pytest.fixture()
def pools() -> Iterator[Callable[..., LaneExecutor]]:
    """Build pools with small options and stop every one after the test."""
    made: list[LaneExecutor] = []

    def make(**options: object) -> LaneExecutor:
        defaults = {"MAX_WORKERS": 2, "MAX_QUEUE": 8, "CONN_IDLE": 60.0}
        executor = LaneExecutor({"OPTIONS": {**defaults, **options}})
        made.append(executor)
        return executor

    yield make
    for executor in made:
        executor.shutdown(5)


@pytest.fixture()
def fake_connections(monkeypatch: pytest.MonkeyPatch) -> _Connections:
    handler = _Connections()
    monkeypatch.setattr(executors, "connections", handler)
    return handler


@pytest.fixture()
def sqlite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, django_db_blocker: DjangoDbBlocker
) -> Iterator[Callable[..., ConnectionHandler]]:
    """Point the pool and the transaction API at one file database built per test."""
    django_db_blocker.unblock()

    def make(**options: object) -> ConnectionHandler:
        database = {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": tmp_path / "db",
            **options,
        }
        handler = ConnectionHandler({"default": database})
        monkeypatch.setattr(executors, "connections", handler)
        monkeypatch.setattr(transaction, "connections", handler)
        return handler

    yield make
    django_db_blocker.restore()


def _select(handler: ConnectionHandler) -> object:
    """Run one query on the default alias and return the raw connection it used."""
    conn = handler["default"]
    with conn.cursor() as cursor:
        cursor.execute("SELECT 1")
    return conn.connection


def _raw(handler: ConnectionHandler) -> object:
    return handler["default"].connection


class TestLaneExecutorThreads:
    """``LaneExecutor`` starts threads on demand, up to its cap."""

    def test_no_thread_exists_before_the_first_submit(self, pools) -> None:
        before = set(threading.enumerate())
        executor = pools()
        assert set(threading.enumerate()) <= before
        assert executor.submit(StealableTask(lambda: 42)).result(5) == 42
        started = set(threading.enumerate()) - before
        assert len(started) == 1
        assert all(thread.name.startswith("next-pool-") for thread in started)
        assert all(thread.daemon for thread in started)

    def test_an_idle_worker_takes_the_next_task_without_a_new_thread(
        self, pools
    ) -> None:
        before = _pool_threads()
        executor = pools()
        executor.submit(StealableTask(lambda: 1)).result(5)
        until(lambda: executor.capacity().busy == 0)
        executor.submit(StealableTask(lambda: 2)).result(5)
        assert len(_pool_threads() - before) == 1

    def test_workers_stop_at_the_cap(self, pools) -> None:
        before = _pool_threads()
        executor = pools(MAX_WORKERS=2)
        gate = threading.Event()
        futures = [executor.submit(_blocked(gate)) for _ in range(4)]
        until(lambda: executor.capacity().busy == 2)
        capacity = executor.capacity()
        assert (capacity.workers, capacity.busy, capacity.queued) == (2, 2, 2)
        assert capacity.idle == 0
        assert len(_pool_threads() - before) == 2
        gate.set()
        assert all(future.result(5) for future in futures)

    def test_a_full_queue_sheds_without_blocking(self, pools) -> None:
        executor = pools(MAX_WORKERS=1, MAX_QUEUE=2)
        gate = threading.Event()
        executor.submit(_blocked(gate))
        until(lambda: executor.capacity().busy == 1)
        queued = [executor.submit(StealableTask(lambda: 0)) for _ in range(2)]
        with pytest.raises(BoundaryShedError):
            executor.submit(StealableTask(lambda: 0))
        assert executor.capacity().queued == 2
        gate.set()
        assert [future.result(5) for future in queued] == [0, 0]

    def test_options_the_entry_omits_come_from_the_settings(self) -> None:
        executor = LaneExecutor()
        assert (executor.max_workers, executor.max_queue) == (16, 64)
        assert executor.conn_idle == 60.0
        options = {"MAX_WORKERS": 3, "CONN_IDLE": "2s"}
        with override_next_settings(DEFER={"EXECUTOR": {"OPTIONS": options}}):
            executor = LaneExecutor({"OPTIONS": {"MAX_QUEUE": 5}})
        assert (executor.max_workers, executor.max_queue) == (3, 5)
        assert executor.conn_idle == 2.0


class TestLaneExecutorOutcomes:
    """Results and failures reach the future, and no failure ends a worker."""

    def test_an_exception_reaches_the_future(self, pools) -> None:
        future = pools().submit(StealableTask(_fail))
        with pytest.raises(ValueError, match="hop failed"):
            future.result(5)

    def test_an_interrupt_reaches_the_future_and_spares_the_worker(
        self, pools, caplog: pytest.LogCaptureFixture
    ) -> None:
        before = _pool_threads()
        executor = pools(MAX_WORKERS=1)
        with caplog.at_level(logging.ERROR, logger="next.suspense"):
            error = executor.submit(StealableTask(_interrupt)).exception(5)
            until(lambda: executor.capacity().busy == 0)
        assert isinstance(error, RuntimeError)
        assert isinstance(error.__cause__, KeyboardInterrupt)
        assert "raised an interrupt" in caplog.text
        assert executor.submit(StealableTask(lambda: "alive")).result(5) == "alive"
        assert len(_pool_threads() - before) == 1


class TestLaneExecutorSteal:
    """A waiter runs a queued task itself, and the worker skips it."""

    def test_a_waiter_runs_a_queued_task_and_the_gauges_follow(
        self, pools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = _Clock()
        monkeypatch.setattr(executors, "time", clock)
        executor = pools(MAX_WORKERS=1)
        gate = threading.Event()
        executor.submit(_blocked(gate))
        until(lambda: executor.capacity().busy == 1)
        ran: dict[str, threading.Thread] = {}

        def recorded(name: str) -> StealableTask[str]:
            def run() -> str:
                ran[name] = threading.current_thread()
                return name

            return StealableTask(run)

        first, middle, last = recorded("a"), recorded("b"), recorded("c")
        executor.submit(first)
        clock.now += 3.0
        executor.submit(middle)
        executor.submit(last)
        assert executor.steal(middle) is True
        assert executor.steal(middle) is False
        assert ran["b"] is threading.current_thread()
        clock.now += 1.0
        capacity = executor.capacity()
        assert capacity.queued == 2
        assert capacity.delay == 4.0
        assert executor.steal(first) is True
        capacity = executor.capacity()
        assert capacity.queued == 1
        assert capacity.delay == 1.0
        gate.set()
        assert last.future.result(5) == "c"
        assert ran["c"].name.startswith("next-pool-")
        assert [first.future.result(0), middle.future.result(0)] == ["a", "b"]
        until(lambda: executor.capacity() == Capacity(1, 0, 0, 0.0))

    def test_a_task_run_directly_leaves_the_queue_at_once(self, pools) -> None:
        executor = pools(MAX_WORKERS=1, MAX_QUEUE=1)
        gate = threading.Event()
        executor.submit(_blocked(gate))
        until(lambda: executor.capacity().busy == 1)
        direct = StealableTask(lambda: "direct")
        executor.submit(direct)
        assert direct.run() is True
        assert executor.capacity().queued == 0
        after = executor.submit(StealableTask(lambda: "after"))
        gate.set()
        assert after.result(5) == "after"
        assert direct.future.result(0) == "direct"

    def test_a_task_never_queued_here_is_not_stolen(self, pools) -> None:
        task = StealableTask(lambda: 1)
        assert pools().steal(task) is False
        assert not task.future.done()

    def test_many_stealers_and_workers_run_each_task_once(self, pools) -> None:
        executor = pools(MAX_WORKERS=4, MAX_QUEUE=400)
        runs = [0] * 400
        lock = threading.Lock()

        def counted(index: int) -> StealableTask[int]:
            def run() -> int:
                with lock:
                    runs[index] += 1
                time.sleep(0)
                return index

            return StealableTask(run)

        tasks = [counted(index) for index in range(400)]
        barrier = threading.Barrier(9)

        def steal(order: list[StealableTask[int]]) -> None:
            barrier.wait(5)
            for task in order:
                executor.steal(task)

        stealers = [
            threading.Thread(
                target=steal, args=(tasks[:: -1 if index % 2 else 1],), daemon=True
            )
            for index in range(8)
        ]
        for stealer in stealers:
            stealer.start()
        for task in tasks:
            executor.submit(task)
        barrier.wait(5)
        for stealer in stealers:
            stealer.join(10)
        assert not any(stealer.is_alive() for stealer in stealers)
        done, _ = wait([task.future for task in tasks], timeout=10)
        assert len(done) == 400
        assert runs == [1] * 400
        assert [task.future.result(0) for task in tasks] == list(range(400))
        until(lambda: executor.capacity() == Capacity(4, 0, 0, 0.0))

    def test_capacity_rereads_a_queue_that_changed_under_it(self, pools) -> None:
        class Shifting(dict):
            moved = False

            def values(self):
                if not self.moved:
                    self.moved = True
                    msg = "dictionary changed size during iteration"
                    raise RuntimeError(msg)
                return super().values()

        executor = pools()
        executor._pending = Shifting({StealableTask(lambda: 1): time.monotonic()})
        capacity = executor.capacity()
        assert capacity.queued == 1
        assert capacity.delay >= 0.0
        executor._pending = {}


class TestLaneExecutorStop:
    """Shutdown, retirement and the fork hook."""

    def test_a_task_that_retires_its_thread_leaves_the_next_to_a_new_one(
        self, pools
    ) -> None:
        executor = pools(MAX_WORKERS=1)

        def retiring() -> threading.Thread:
            retire_thread()
            return threading.current_thread()

        retired = executor.submit(StealableTask(retiring)).result(5)
        retired.join(5)
        assert not retired.is_alive()
        current = StealableTask(threading.current_thread)
        assert executor.submit(current).result(5) is not retired

    def test_retiring_a_thread_no_pool_owns_does_nothing(self) -> None:
        retire_thread()
        assert threading.current_thread().is_alive()

    def test_shutdown_fails_queued_tasks_and_lets_running_ones_finish(
        self, pools
    ) -> None:
        before = _pool_threads()
        executor = pools(MAX_WORKERS=1)
        gate = threading.Event()
        running = executor.submit(StealableTask(lambda: gate.wait(5) and "finished"))
        until(lambda: executor.capacity().busy == 1)
        queued = executor.submit(StealableTask(lambda: "never"))
        queued.add_done_callback(lambda _: gate.set())
        executor.shutdown(5)
        assert running.result(0) == "finished"
        assert isinstance(queued.exception(0), ExecutorStoppedError)
        assert not _pool_threads() - before
        with pytest.raises(ExecutorStoppedError):
            executor.submit(StealableTask(lambda: 0))

    def test_an_idle_worker_retires_and_a_later_submit_starts_one(self, pools) -> None:
        before = _pool_threads()
        executor = pools(MAX_WORKERS=1, CONN_IDLE=0.01)
        executor.linger = 0.02
        executor.submit(StealableTask(lambda: 1)).result(5)
        started = _pool_threads() - before
        for thread in started:
            thread.join(5)
        assert not any(thread.is_alive() for thread in started)
        assert not _pool_threads() - before
        assert executor.submit(StealableTask(lambda: 2)).result(5) == 2

    def test_a_worker_stays_while_queued_tasks_need_it(self, pools) -> None:
        executor = pools(MAX_WORKERS=1)
        worker = _Worker(lambda _: None, "next-pool-test")
        executor._workers = (worker,)
        executor._pending[StealableTask(lambda: 1)] = time.monotonic()
        assert executor._retire(worker) is False
        assert executor._workers == (worker,)
        executor._pending.clear()
        assert executor._retire(worker) is True
        assert executor._workers == ()

    def test_a_stopped_pool_starts_no_worker(self, pools) -> None:
        executor = pools(MAX_WORKERS=1)
        executor._pending[StealableTask(lambda: 1)] = time.monotonic()
        executor._stopped = True
        executor._staff()
        assert executor._workers == ()

    def test_the_fork_hook_drops_inherited_workers_and_queue(
        self, pools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = _pool_threads()
        executor = pools(MAX_WORKERS=1, CONN_IDLE=0.01)
        executor.linger = 0.01
        monkeypatch.setattr(executors, "_live", weakref.WeakSet([executor]))
        gate = threading.Event()
        executor.submit(_blocked(gate))
        executor.submit(StealableTask(lambda: 1))
        until(lambda: executor.capacity().busy == 1)
        inherited = list(_pool_threads() - before)
        _forget_pools()
        assert executor.capacity() == Capacity(1, 0, 0, 0.0)
        assert executor.submit(StealableTask(lambda: "fresh")).result(5) == "fresh"
        gate.set()
        for thread in inherited:
            thread.join(5)
        assert not any(thread.is_alive() for thread in inherited)


class TestLaneExecutorConnections:
    """The pool closes, checks and returns the connections of its own threads."""

    def test_an_idle_worker_closes_its_connections(
        self, pools, fake_connections: _Connections
    ) -> None:
        held = _Connection()
        executor = pools(MAX_WORKERS=1, CONN_IDLE=0.02)
        executor.submit(StealableTask(lambda: fake_connections.hold(held))).result(5)
        assert held.closed.wait(5)
        assert held.connection is None

    def test_each_task_crosses_the_request_boundary_check(
        self, pools, fake_connections: _Connections
    ) -> None:
        held = _Connection()
        executor = pools(MAX_WORKERS=1)
        executor.submit(StealableTask(lambda: fake_connections.hold(held))).result(5)
        until(lambda: held.refreshed == 1)
        assert executor.submit(StealableTask(lambda: held.refreshed)).result(5) == 2
        until(lambda: held.refreshed == 3)

    def test_a_connection_past_its_max_age_closes_after_the_task(
        self, pools, sqlite: Callable[..., ConnectionHandler]
    ) -> None:
        handler = sqlite(CONN_MAX_AGE=0)
        executor = pools(MAX_WORKERS=1)
        executor.submit(StealableTask(lambda: _select(handler))).result(5)
        until(lambda: executor.capacity().busy == 0)
        assert executor.submit(StealableTask(lambda: _raw(handler))).result(5) is None

    def test_a_persistent_connection_serves_the_next_task(
        self, pools, sqlite: Callable[..., ConnectionHandler]
    ) -> None:
        handler = sqlite(CONN_MAX_AGE=None)
        executor = pools(MAX_WORKERS=1)
        first = executor.submit(StealableTask(lambda: _select(handler))).result(5)
        second = executor.submit(StealableTask(lambda: _select(handler))).result(5)
        assert first is second

    def test_the_health_check_runs_again_in_each_task(
        self, pools, sqlite: Callable[..., ConnectionHandler]
    ) -> None:
        handler = sqlite(CONN_MAX_AGE=None, CONN_HEALTH_CHECKS=True)
        executor = pools(MAX_WORKERS=1)
        first = executor.submit(StealableTask(lambda: _select(handler))).result(5)

        def failed_over() -> object:
            conn = handler["default"]
            conn.is_usable = lambda: False
            try:
                return _select(handler)
            finally:
                del conn.is_usable

        second = executor.submit(StealableTask(failed_over)).result(5)
        assert second is not first

    def test_an_atomic_block_a_task_left_open_never_reaches_the_next_task(
        self,
        pools,
        sqlite: Callable[..., ConnectionHandler],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        handler = sqlite(CONN_MAX_AGE=None)
        executor = pools(MAX_WORKERS=1)

        def execute(sql: str) -> None:
            with handler["default"].cursor() as cursor:
                cursor.execute(sql)

        def rows() -> tuple[int, bool]:
            with handler["default"].cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM t")
                (count,) = cursor.fetchone()
            return count, handler["default"].in_atomic_block

        def leave_open() -> None:
            transaction.atomic().__enter__()
            execute("INSERT INTO t VALUES (1)")

        executor.submit(
            StealableTask(lambda: execute("CREATE TABLE t (x int)"))
        ).result(5)
        with caplog.at_level(logging.WARNING, logger="next.suspense"):
            for _ in range(2):
                executor.submit(StealableTask(leave_open)).result(5)
                assert executor.submit(StealableTask(rows)).result(5) == (0, False)
        executor.submit(
            StealableTask(lambda: execute("INSERT INTO t VALUES (2)"))
        ).result(5)
        assert executor.submit(StealableTask(rows)).result(5) == (1, False)
        warnings = [r for r in caplog.records if "inside an atomic block" in r.message]
        assert len(warnings) == 1

    @pytest.mark.django_db()
    def test_execute_wrappers_a_task_leaves_never_reach_the_next_task(
        self, pools, fake_connections: _Connections
    ) -> None:
        executor = pools(MAX_WORKERS=1)
        held = _Connection()

        def kept(execute: Callable[..., object], *args: object) -> object:
            return execute(*args)

        def leaked(execute: Callable[..., object], *args: object) -> object:
            return execute(*args)

        def open_with_one() -> None:
            fake_connections.hold(held)
            held.execute_wrappers.append(kept)

        def leave_another() -> int:
            held.execute_wrappers.append(leaked)
            return len(held.execute_wrappers)

        executor.submit(StealableTask(open_with_one)).result(5)
        assert held.execute_wrappers == []
        held.execute_wrappers.append(kept)
        assert executor.submit(StealableTask(leave_another)).result(5) == 2
        assert held.execute_wrappers == [kept]
