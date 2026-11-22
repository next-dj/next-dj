"""The process background loop, the pool lane and the request-thread hop executor."""

import asyncio
import functools
import itertools
import logging
import threading
import time
import weakref
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import Future, wait
from contextlib import contextmanager, suppress
from queue import Empty, SimpleQueue
from typing import Any, override

from asgiref.current_thread_executor import CurrentThreadExecutor
from asgiref.local import Local
from asgiref.sync import AsyncToSync, SyncToAsync
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError, InterfaceError, connections
from django.db.backends.base.base import BaseDatabaseWrapper

from next.conf.defer import backend_count, backend_option, entry_options
from next.defer import parse_duration
from next.suspense.errors import BoundaryShedError, ExecutorStoppedError
from next.suspense.markers import Capacity
from next.tasks import QuitParentExecutor, StealableTask
from next.utils import after_fork


logger = logging.getLogger("next.suspense")

_THREAD_NAME = "next-background-loop"
_POOL_THREAD = "next-pool"
_STOPPED = "The background loop stopped before the coroutine finished."
_WALKED = ("_work_ready", "_broken", "_old_executor")
_UNSERVED = "The request thread no longer serves this coroutine."


def _route(record: logging.LogRecord) -> bool:
    """Hand the asyncio records of the background loop thread to the package logger."""
    if threading.current_thread().name != _THREAD_NAME:
        return True
    logger.log(record.levelno, "%s", record.getMessage(), exc_info=record.exc_info)
    return False


def _run(loop: asyncio.AbstractEventLoop, done: Callable[[], bool]) -> None:
    """Run `loop` until a stop finds `done` true, reporting interrupts a task lets out.

    A task re-raises `KeyboardInterrupt` and `SystemExit` out of the loop it runs on.
    """
    while True:
        try:
            loop.run_forever()
        except (KeyboardInterrupt, SystemExit) as error:
            loop.call_exception_handler(
                {
                    "message": "A task raised an interrupt on the loop.",
                    "exception": error,
                }
            )
        if done():
            return


class _LoopThread:
    """One event loop and the daemon thread that runs it until halted."""

    def __init__(self) -> None:
        """Create the loop, with slow-callback detection in `DEBUG`, and start it."""
        self.loop = asyncio.new_event_loop()
        if settings.DEBUG:
            self.loop.set_debug(enabled=True)
            self.loop.slow_callback_duration = 0.1
        logging.getLogger("asyncio").addFilter(_route)
        self.halted = False
        self.thread = threading.Thread(
            target=self._serve, name=_THREAD_NAME, daemon=True
        )
        self.thread.start()

    @property
    def serving(self) -> bool:
        """Whether the thread still runs the loop."""
        return self.thread.is_alive() and not self.loop.is_closed()

    def halt(self) -> None:
        """Stop the loop after every callback scheduled before this call."""
        self.loop.call_soon_threadsafe(self._halt)

    def _halt(self) -> None:
        self.halted = True
        self.loop.stop()

    def _serve(self) -> None:
        """Run the loop until halted, then cancel and settle its tasks and close it."""
        _run(self.loop, lambda: self.halted)
        tasks = asyncio.all_tasks(self.loop)
        for task in tasks:
            task.cancel(_STOPPED)
        if tasks:
            gathered = asyncio.gather(*tasks, return_exceptions=True)
            gathered.add_done_callback(lambda _: self.loop.stop())
            _run(self.loop, gathered.done)
        self.loop.close()


class BackgroundLoop:
    """One daemon thread running one event loop, started by the first caller.

    Callers outside a server loop run coroutines here instead of a loop per request.
    """

    def __init__(self) -> None:
        """Hold no loop and no thread until `get` is called."""
        self.forget()

    def forget(self) -> None:
        """Drop the loop, its thread and the lock, as a forked child must."""
        self._lock = threading.Lock()
        self._running: _LoopThread | None = None

    def current(self) -> asyncio.AbstractEventLoop | None:
        """Return the running loop without starting one."""
        running = self._running
        return None if running is None else running.loop

    def _ensure(self) -> _LoopThread:
        if self._running is None or not self._running.serving:
            self._running = _LoopThread()
        return self._running

    def get(self) -> asyncio.AbstractEventLoop:
        """Return the loop, starting a thread when none serves one."""
        with self._lock:
            return self._ensure().loop

    def call_soon[*Ts](
        self, callback: Callable[[*Ts], object], *args: *Ts
    ) -> asyncio.AbstractEventLoop:
        """Schedule `callback` on the loop and return the loop.

        Holding the lock orders it ahead of a concurrent `stop`, which settles it.
        """
        with self._lock:
            loop = self._ensure().loop
            loop.call_soon_threadsafe(callback, *args)
        return loop

    def stop(self, timeout: float = 5.0) -> None:
        """Cancel the loop's tasks, let them settle, close it and join its thread."""
        with self._lock:
            running, self._running = self._running, None
            if running is None or not running.serving:
                return
            running.halt()
        running.thread.join(timeout)


background_loop = BackgroundLoop()
after_fork(background_loop.forget)


@functools.cache
def _asgiref_gaps() -> tuple[str, ...]:
    """Name the asgiref attributes hop routing reads that the installed one lacks."""
    try:
        walked = vars(CurrentThreadExecutor(None))
    except TypeError:
        walked = {}
    present = {
        "AsyncToSync.executors": isinstance(
            getattr(AsyncToSync, "executors", None), Local
        ),
        "SyncToAsync.threadlocal": isinstance(
            getattr(SyncToAsync, "threadlocal", None), threading.local
        ),
    } | {f"CurrentThreadExecutor.{name}": name in walked for name in _WALKED}
    return tuple(name for name, found in present.items() if not found)


def check_asgiref() -> None:
    """Refuse to drive coroutines when asgiref lacks the attributes hop routing uses.

    The first call inspects asgiref and later calls reuse its verdict, failure included.
    """
    missing = _asgiref_gaps()
    if not missing:
        return
    msg = (
        f"The installed asgiref lacks {', '.join(missing)}, which routes the "
        "thread-sensitive calls of async page code to the request thread. Install "
        "asgiref 3.9 or later."
    )
    raise ImproperlyConfigured(msg)


class RequestThreadExecutor(QuitParentExecutor):
    """Queue thread-sensitive hops for the request thread, which runs them waiting."""

    def __init__(self) -> None:
        """Open an empty queue served by the thread that creates it."""
        self._queue: SimpleQueue[StealableTask[Any] | None] = SimpleQueue()
        self._closed = False
        self._thread = threading.current_thread()

    @property
    def closed(self) -> bool:
        """Whether the executor rejects hops, as its coroutine has finished."""
        return self._closed

    @property
    def thread(self) -> threading.Thread:
        """Return the thread that serves the hops."""
        return self._thread

    @override
    def submit[**P, R](
        self, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs
    ) -> Future[R]:
        """Queue the call without blocking, failing it once the executor is closed."""
        if threading.current_thread() is self._thread:
            msg = "You cannot submit onto RequestThreadExecutor from its own thread"
            raise RuntimeError(msg)
        hop = StealableTask(functools.partial(fn, *args, **kwargs))
        self._queue.put(hop)
        if self._closed:
            hop.reject(RuntimeError(_UNSERVED))
        return hop.future

    def wake(self) -> None:
        """Unblock the serving thread so it rechecks what it waits for."""
        self._queue.put(None)

    def serve(self, future: Future[Any], timeout: float | None = None) -> None:
        """Run queued hops on the calling thread until `future` is done or time is up.

        A zero `timeout` runs the hops already queued and returns without waiting.
        """
        saved = dict(vars(SyncToAsync.threadlocal))
        until = None if timeout is None else time.monotonic() + timeout
        while not future.done():
            try:
                hop = self._next(until)
            except Empty:
                return
            if hop is None:
                continue
            if future.done():
                self._queue.put(hop)
                return
            _run_keeping_markers(hop, saved)

    def _next(self, until: float | None) -> StealableTask[Any] | None:
        """Take the next hop, raising `Empty` once the instant `until` has passed."""
        if until is None:
            return self._queue.get()
        return self._queue.get(timeout=max(0.0, until - time.monotonic()))

    def close(self) -> None:
        """Reject queued and later hops, since the coroutine has finished by now."""
        self._closed = True
        while True:
            try:
                hop = self._queue.get_nowait()
            except Empty:
                return
            if hop is not None:
                hop.reject(RuntimeError(_UNSERVED))


def _run_keeping_markers(hop: StealableTask[Any], saved: dict[str, Any]) -> None:
    """Run `hop` and put back the loop markers asgiref leaves on this thread.

    A stale marker would send a later `async_to_sync` here to the background loop.
    """
    started = time.thread_time_ns()
    try:
        hop.run()
    finally:
        markers = vars(SyncToAsync.threadlocal)
        markers.clear()
        markers.update(saved)
        _served.cpu = served_cpu() + time.thread_time_ns() - started


_serving = threading.local()
_served = threading.local()


def served_cpu() -> int:
    """Return the CPU nanoseconds this thread spent on hops it served for others."""
    cpu: int = getattr(_served, "cpu", 0)
    return cpu


@contextmanager
def request_executor() -> Iterator[RequestThreadExecutor]:
    """Yield the executor this thread serves, opened and closed by the outermost caller.

    A hop that drives again shares it, and a close on another thread spares this local.
    """
    current: RequestThreadExecutor | None = getattr(_serving, "executor", None)
    if current is not None and not current.closed:
        yield current
        return
    executor = RequestThreadExecutor()
    _serving.executor = executor
    try:
        yield executor
    finally:
        executor.close()
        if threading.current_thread() is executor.thread:
            del _serving.executor


def serve_until(future: Future[Any], /) -> None:
    """Block until `future` is done, running the hops queued for this thread meanwhile.

    A thread no driver serves, such as a pool thread, has no hops and waits plainly.
    """
    executor: RequestThreadExecutor | None = getattr(_serving, "executor", None)
    if executor is None or executor.closed:
        wait((future,))
        return
    future.add_done_callback(lambda _: executor.wake())
    executor.serve(future)


class BoundaryExecutor(ABC):
    """Run the sync jobs and blocking segments the coordinator places on the pool lane.

    Each unit is a `StealableTask`, so a waiter that finds one unstarted runs it itself.
    """

    @abstractmethod
    def submit[R](self, task: StealableTask[R]) -> Future[R]:
        """Queue `task` without blocking and return its future, raising when refused."""

    @abstractmethod
    def steal(self, task: StealableTask[Any]) -> bool:
        """Run a still queued `task` on this thread, false when a worker has it."""

    @abstractmethod
    def capacity(self) -> Capacity:
        """Return pool size, busy slots, queue depth and the oldest queue delay."""

    @abstractmethod
    def shutdown(self, timeout: float = 5.0) -> None:
        """Refuse new tasks, fail queued ones and wait up to `timeout` for the rest."""


class _Worker:
    """One pool thread and the task it runs."""

    __slots__ = ("current", "retiring", "thread")

    def __init__(self, serve: Callable[["_Worker"], None], name: str) -> None:
        """Create the thread that serves this worker, not started yet."""
        self.current: StealableTask[Any] | None = None
        self.retiring = False
        self.thread = threading.Thread(
            target=serve, args=(self,), name=name, daemon=True
        )


_numbers = itertools.count(1)
_pool_thread = threading.local()


def retire_thread() -> None:
    """Make the pool worker running this call exit once its task returns.

    A task whose thread-local state can no longer be trusted calls it, as the state dies
    with the thread. It does nothing on a thread no pool owns.
    """
    worker: _Worker | None = getattr(_pool_thread, "worker", None)
    if worker is not None:
        worker.retiring = True


type _Wrappers = dict[str, list[Callable[..., object]]]


def _wrappers() -> _Wrappers:
    """Return the execute wrappers of every connection this thread has opened."""
    return {
        conn.alias: list(conn.execute_wrappers)
        for conn in connections.all(initialized_only=True)
    }


def _restore_wrappers(saved: _Wrappers) -> None:
    """Put back the execute wrappers each connection had before a task, none if new."""
    for conn in connections.all(initialized_only=True):
        conn.execute_wrappers = list(saved.get(conn.alias, ()))


def _close(conn: BaseDatabaseWrapper) -> None:
    """Close one connection, a failing close leaving it closed all the same."""
    with suppress(DatabaseError, InterfaceError):
        conn.close()


def _close_connections() -> None:
    """Close every connection this thread opened, as an idle worker holds none."""
    for conn in connections.all(initialized_only=True):
        _close(conn)


def _refresh(conn: BaseDatabaseWrapper) -> None:
    """Close `conn` when broken or past `CONN_MAX_AGE`, rearming its health check.

    It is the check Django runs on every connection as a request starts and finishes.
    """
    with suppress(DatabaseError, InterfaceError):
        conn.close_if_unusable_or_obsolete()


def _refresh_connections() -> None:
    """Run the request-boundary check on every connection this thread opened."""
    for conn in connections.all(initialized_only=True):
        _refresh(conn)


class LaneExecutor(BoundaryExecutor):
    """The bounded process-wide pool, its threads started on demand up to `MAX_WORKERS`.

    A worker idle for `CONN_IDLE` closes its connections and exits `linger` later.
    """

    linger: float = 300.0

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Read the `OPTIONS` of the settings entry, starting no thread."""
        options = entry_options(config)
        self.max_workers = backend_count(options, "EXECUTOR", "MAX_WORKERS")
        self.max_queue = backend_count(options, "EXECUTOR", "MAX_QUEUE")
        self.conn_idle = parse_duration(
            backend_option(options, "EXECUTOR", "CONN_IDLE")
        )
        self._stopped = False
        self._discarded: set[str] = set()
        self.forget()
        _live.add(self)

    def forget(self) -> None:
        """Drop the workers and the queue, as a forked child inherits no thread."""
        self._gate = threading.Lock()
        self._staffing = threading.Lock()
        self._queue: SimpleQueue[StealableTask[Any] | None] = SimpleQueue()
        self._pending: dict[StealableTask[Any], float] = {}
        self._workers: tuple[_Worker, ...] = ()

    @override
    def submit[R](self, task: StealableTask[R]) -> Future[R]:
        """Queue `task` and staff a worker for it, refusing it when full or stopped."""
        with self._gate:
            if self._stopped:
                raise ExecutorStoppedError
            if len(self._pending) >= self.max_queue:
                raise BoundaryShedError
            self._pending[task] = time.monotonic()
        task.when_claimed(functools.partial(self._pending.pop, task, None))
        self._queue.put(task)
        self._staff()
        return task.future

    @override
    def steal(self, task: StealableTask[Any]) -> bool:
        """Run a still queued `task` here, and the worker that dequeues it skips it."""
        if task not in self._pending:
            return False
        return task.run()

    @override
    def capacity(self) -> Capacity:
        """Read the gauges without a lock, each worker publishing its own task."""
        busy = sum(worker.current is not None for worker in self._workers)
        pending = self._pending
        queued = len(pending)
        try:
            oldest = next(iter(pending.values()), None)
        except RuntimeError:
            oldest = next(iter(pending.copy().values()), None)
        delay = 0.0 if oldest is None else max(0.0, time.monotonic() - oldest)
        return Capacity(self.max_workers, busy, queued, delay)

    @override
    def shutdown(self, timeout: float = 5.0) -> None:
        """Refuse new tasks, fail the queued ones and join the workers up to `timeout`.

        A running task finishes first, since no thread is stopped from outside.
        """
        with self._gate:
            self._stopped = True
        with suppress(KeyError):
            while True:
                task, _ = self._pending.popitem()
                task.reject(ExecutorStoppedError())
        with self._staffing:
            workers = self._workers
        for _ in workers:
            self._queue.put(None)
        deadline = time.monotonic() + timeout
        for worker in workers:
            worker.thread.join(max(0.0, deadline - time.monotonic()))

    def _understaffed(self, workers: tuple[_Worker, ...]) -> bool:
        """Whether queued tasks outnumber idle `workers` and the cap allows one more.

        The queue is read first, as a worker marks itself busy before it dequeues.
        """
        queued = len(self._pending)
        idle = sum(worker.current is None for worker in workers)
        return len(workers) < self.max_workers and queued > idle

    def _staff(self) -> None:
        """Start a worker when every one is busy with tasks still queued."""
        if not self._understaffed(self._workers):
            return
        with self._staffing:
            workers = self._workers
            if self._stopped or not self._understaffed(workers):
                return
            worker = _Worker(self._serve, f"{_POOL_THREAD}-{next(_numbers)}")
            self._workers = (*workers, worker)
            worker.thread.start()

    def _serve(self, worker: _Worker) -> None:
        """Run queued tasks until a stop or a retirement, closing connections after."""
        _pool_thread.worker = worker
        try:
            self._work(worker)
        finally:
            _close_connections()
            self._leave(worker)

    def _work(self, worker: _Worker) -> None:
        """Take tasks, close connections after `CONN_IDLE` and retire `linger` later."""
        rested = False
        while True:
            try:
                task = self._queue.get(
                    timeout=self.linger if rested else self.conn_idle
                )
            except Empty:
                if rested and self._retire(worker):
                    return
                _close_connections()
                rested = True
                continue
            if task is None:
                return
            if self._take(worker, task):
                rested = False
            if worker.retiring:
                return

    def _take(self, worker: _Worker, task: StealableTask[Any]) -> bool:
        """Run `task` unless a waiter stole it, its slot held until it returns.

        Each connection gets back the execute wrappers it had, none outliving the task.
        """
        worker.current = task
        try:
            _refresh_connections()
            saved = _wrappers()
            try:
                ran = task.run()
            except BaseException:
                logger.exception("A pool task raised an interrupt, kept on its future.")
                ran = True
            if not ran:
                return False
            _restore_wrappers(saved)
            self._release_connections()
        finally:
            worker.current = None
        return True

    def _release_connections(self) -> None:
        """Discard a connection a task left in an atomic block, check the others."""
        for conn in connections.all(initialized_only=True):
            if conn.in_atomic_block:
                self._discard(conn)
            else:
                _refresh(conn)

    def _discard(self, conn: BaseDatabaseWrapper) -> None:
        """Close `conn`, which ends its transaction, so no next task inherits it.

        No public call exits a block whose `Atomic` the task dropped, so the flag goes.
        """
        if conn.alias not in self._discarded:
            self._discarded.add(conn.alias)
            logger.warning(
                "A pool task left the %r connection inside an atomic block, so the "
                "pool closed it, which rolls its transaction back.",
                conn.alias,
            )
        conn.in_atomic_block = False
        conn.closed_in_transaction = False
        _close(conn)

    def _retire(self, worker: _Worker) -> bool:
        """Leave the pool unless the queued tasks still need this worker."""
        with self._staffing:
            rest = tuple(other for other in self._workers if other is not worker)
            self._workers = rest
            if self._stopped or not self._understaffed(rest):
                return True
            self._workers = (*rest, worker)
            return False

    def _leave(self, worker: _Worker) -> None:
        """Drop a worker that stopped and staff a successor for tasks still queued."""
        with self._staffing:
            self._workers = tuple(
                other for other in self._workers if other is not worker
            )
        self._staff()


_live: weakref.WeakSet[LaneExecutor] = weakref.WeakSet()


def _forget_pools() -> None:
    """Drop the inherited workers and queue of every pool in a forked child."""
    for executor in tuple(_live):
        executor.forget()


after_fork(_forget_pools)


__all__ = [
    "BackgroundLoop",
    "BoundaryExecutor",
    "LaneExecutor",
    "RequestThreadExecutor",
    "background_loop",
    "check_asgiref",
    "request_executor",
    "retire_thread",
    "serve_until",
    "served_cpu",
]
