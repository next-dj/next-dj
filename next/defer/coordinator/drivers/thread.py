"""The driver of one response on its request thread, a WSGI worker or a plain thread."""

import math
import threading
import time
from collections import deque
from collections.abc import Generator
from concurrent.futures import Future
from functools import partial
from queue import Empty, SimpleQueue
from typing import override

from next.bridge.tasks import StealableTask
from next.defer.coordinator import runner
from next.defer.coordinator.machine import Emit, Unit
from next.defer.ledger import ResponseJobs
from next.defer.markers import Capacity, Lane, ServerKind

from .base import (
    FAILED,
    IDLE,
    Closed,
    Driver,
    Idle,
    Message,
    Owner,
    Run,
    error_of,
    logger,
)


class ThreadDriver(Driver):
    """Drive one response from its request thread, inside the body generator.

    With nothing to write the thread runs request-lane units and steals pool units.
    """

    def __init__(
        self,
        owner: Owner,
        session: ResponseJobs,
        *,
        idle: float | None = None,
        buffered: bool = False,
    ) -> None:
        """Open the queue every lane reports into, read by the request thread only."""
        super().__init__(owner, session, ServerKind.WSGI_THREADED)
        self._buffered = buffered
        self._inbox: SimpleQueue[Message] = SimpleQueue()
        self._backlog: deque[Run] = deque()
        self._stealable: deque[StealableTask[None]] = deque()
        self._wake_at = session.deadline + session.caps.write_grace
        self._idle = math.inf if idle is None else idle
        self._idle_at = math.inf

    def run(self) -> Generator[Emit | Idle, None, None]:
        """Yield the batches, each request for the next one acknowledging the last.

        An `IDLE` comes only after the batch before it was acknowledged, holding none.
        """
        self._request_thread = threading.get_ident()
        try:
            self._open()
            self._idle_at = time.monotonic() + self._idle
            while self._finish is None:
                batch: Emit | Idle
                if self._emits:
                    batch = self._emits.popleft()
                elif time.monotonic() >= self._idle_at:
                    batch = IDLE
                else:
                    self._step()
                    continue
                try:
                    yield batch
                except BaseException:
                    self._consumer = True
                    raise
                if isinstance(batch, Emit):
                    self._acknowledge()
                self._idle_at = time.monotonic() + self._idle
        except Exception:
            if self._consumer:
                raise
            self._logged = True
            logger.exception(FAILED)
        finally:
            self._close()

    def _step(self) -> None:
        """Take what arrived, else run, else steal, else wait."""
        if self._collect():
            return
        if self._backlog:
            self._run_backlog()
            return
        capacity = self._executor.capacity()
        self._take(self._machine.capacity(capacity, time.monotonic()))
        if self._emits or self._backlog or self._finish is not None:
            return
        if self._inbox.empty() and not self._steal(capacity):
            self._block()

    def _collect(self) -> bool:
        received = False
        while True:
            try:
                message = self._inbox.get_nowait()
            except Empty:
                break
            received = True
            self._receive(message)
        return received

    def _run_backlog(self) -> None:
        """Run the next request-lane unit here, reporting each job straight away.

        It counts only from now, as the units queued behind it wait for its end.
        """
        run = self._backlog.popleft()
        unit = run.unit
        with run.counted as counted:
            for task in unit.tasks:
                self._report(
                    runner.run(
                        self._runtime,
                        unit.id,
                        task,
                        unit.placement,
                        lane=Lane.REQUEST,
                        queued=run.queued,
                        count=counted.job,
                    )
                )
        self._shut(run)

    def _steal(self, capacity: Capacity) -> bool:
        """Run the oldest pool unit of this response no worker has started yet.

        A stream steals only if no worker is free and none of its own units runs.
        """
        if not self._buffered and (capacity.idle or self._flying()):
            return False
        while self._stealable:
            if self._executor.steal(self._stealable.popleft()):
                return True
        return False

    def _flying(self) -> bool:
        """Whether a unit of this response runs on a worker right now."""
        for run in self._running:
            work = run.work
            if work is not None and work.future.running():
                return True
        return False

    def _block(self) -> None:
        """Wait for a report, the next wake or the idle instant."""
        until = min(self._wake_at, self._idle_at)
        try:
            message = self._inbox.get(timeout=max(0.0, until - time.monotonic()))
        except Empty:
            message = None
        if message is not None:
            self._receive(message)
        if time.monotonic() >= self._wake_at:
            self._tick()

    @override
    def _dispatch(self, run: Run) -> StealableTask[None]:
        work = StealableTask(partial(self._pool_body, run))
        self._executor.submit(work)
        return work

    @override
    def _post(self, message: Message) -> None:
        self._inbox.put(message)

    @override
    def _watch(self, run: Run, work: StealableTask[None]) -> None:
        self._stealable.append(work)
        work.future.add_done_callback(partial(self._pool_done, run))

    def _pool_done(self, run: Run, future: Future[None]) -> None:
        self._post(Closed(run, error_of(future)))

    @override
    def _request(self, units: tuple[Unit, ...]) -> None:
        for unit in units:
            self._backlog.append(self._register(unit, Lane.REQUEST))

    @override
    def _wake(self, at: float) -> None:
        self._wake_at = at

    @override
    def _release(self) -> None:
        self._backlog.clear()
        self._stealable.clear()


__all__ = ["ThreadDriver"]
