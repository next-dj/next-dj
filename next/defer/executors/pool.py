"""The bounded pool lane that runs the jobs the coordinator places on it."""

import atexit
import functools
import itertools
import logging
import threading
import time
import weakref
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from contextlib import suppress
from queue import Empty, SimpleQueue
from typing import Any, override

from next.bridge.loop import background_loop
from next.bridge.tasks import StealableTask, contextless
from next.caches import FORK
from next.conf.defer import backend_count, backend_seconds, entry_options
from next.defer.errors import BoundaryShedError, ExecutorStoppedError
from next.defer.markers import Capacity

from .hygiene import (
    close_connections,
    refresh_connections,
    release_connections,
    restore_wrappers,
    wrappers,
)


logger = logging.getLogger("next.defer")

_POOL_THREAD = "next-pool"
_KEY = "EXECUTOR"


class BoundaryExecutor(ABC):
    """Run the sync jobs and blocking segments the coordinator places on the pool lane.

    Each unit is a `StealableTask`, so a waiter that finds one unstarted runs it itself.
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Keep the `OPTIONS` of the settings entry."""
        self.options: Mapping[str, object] = entry_options(config)

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


class LaneExecutor(BoundaryExecutor):
    """The bounded process-wide pool, its threads started on demand up to `MAX_WORKERS`.

    A worker idle for `CONN_IDLE` closes its connections and exits `linger` later.
    """

    linger: float = 300.0

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Size the pool from the `OPTIONS` of `config`, any it omits from `DEFER`."""
        super().__init__(config)
        self.max_workers = backend_count(self.options, _KEY, "MAX_WORKERS")
        self.max_queue = backend_count(self.options, _KEY, "MAX_QUEUE")
        self.conn_idle = backend_seconds(self.options, _KEY, "CONN_IDLE")
        self.shutdown_timeout = backend_seconds(self.options, _KEY, "SHUTDOWN_TIMEOUT")
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
    def shutdown(self, timeout: float | None = None) -> None:
        """Refuse new tasks, fail the queued ones and join the workers up to `timeout`.

        A running task finishes first, and a `timeout` of None waits `SHUTDOWN_TIMEOUT`.
        """
        if timeout is None:
            timeout = self.shutdown_timeout
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
            contextless(worker.thread.start)

    def _serve(self, worker: _Worker) -> None:
        """Run queued tasks until a stop or a retirement, closing connections after."""
        _pool_thread.worker = worker
        try:
            self._work(worker)
        finally:
            close_connections()
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
                close_connections()
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
            refresh_connections()
            saved = wrappers()
            try:
                ran = task.run()
            except BaseException:
                logger.exception("A pool task raised an interrupt, kept on its future.")
                ran = True
            if not ran:
                return False
            restore_wrappers(saved)
            release_connections(self._discarded)
        finally:
            worker.current = None
        return True

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


FORK.attach(_forget_pools)


def _stop_at_exit() -> None:
    """Stop every pool and then the background loop, as the interpreter exits.

    The server has returned by then, and the late jobs of daemon workers get the grace
    of their pool before the interpreter drops them.
    """
    for executor in tuple(_live):
        executor.shutdown()
    background_loop.stop()


atexit.register(_stop_at_exit)


__all__ = ["BoundaryExecutor", "LaneExecutor", "retire_thread"]
