"""The driver of one response on the server loop, every wait an await."""

import asyncio
import contextvars
import threading
import time
from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Coroutine, Iterable
from contextlib import suppress
from functools import partial
from typing import Any, cast, override

from asgiref.sync import sync_to_async

from next.bridge.hops import Handoff
from next.bridge.tasks import StealableTask
from next.conf.defer import defer_seconds
from next.defer.coordinator import runner
from next.defer.coordinator.machine import Emit, Unit
from next.defer.ledger import ResponseJobs
from next.defer.markers import Lane, ServerKind
from next.deps.loader import drain_loaders

from .base import (
    COALESCE,
    FAILED,
    TICK,
    Closed,
    Driver,
    Message,
    Owner,
    Refused,
    Run,
    error_of,
    logger,
)
from .permits import RequestPermits, request_permits


async def _outlast(tasks: Iterable[asyncio.Future[Any]]) -> bool:
    """Wait until all of `tasks` are done, saying whether a cancel came meanwhile."""
    pending = {task for task in tasks if not task.done()}
    cancelled = False
    while pending:
        try:
            await asyncio.wait(pending)
        except asyncio.CancelledError:
            cancelled = True
        pending = {task for task in pending if not task.done()}
    return cancelled


class AsyncDriver(Driver):
    """Drive one response on the server loop, inside the async body iterator.

    A response cannot end before its running request-lane job returns.
    """

    _loop: asyncio.AbstractEventLoop
    _permits: RequestPermits

    def __init__(self, owner: Owner, session: ResponseJobs) -> None:
        """Hold no loop until the first batch is asked for."""
        super().__init__(owner, session, ServerKind.ASGI)
        self._queue: asyncio.Queue[Message] = asyncio.Queue()
        self._loop_thread: int | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._batches: set[asyncio.Task[None]] = set()
        self._hopping: set[asyncio.Task[None]] = set()
        self._dispatched: set[asyncio.Task[None]] = set()
        self._lane: Handoff | None = None
        self._queued: deque[Run] = deque()
        self._queue_timeout = defer_seconds("EXECUTOR", "QUEUE_TIMEOUT")

    async def run(self) -> AsyncGenerator[Emit, None]:
        """Yield the batches, each request for the next one acknowledging the last."""
        self._loop = asyncio.get_running_loop()
        self._loop_thread = threading.get_ident()
        try:
            self._permits = request_permits()
            self._open()
            while self._finish is None:
                if self._emits:
                    emit = self._emits.popleft()
                    try:
                        yield emit
                    except BaseException:
                        self._consumer = True
                        raise
                    self._acknowledge()
                elif not self._collect():
                    await self._next_event()
        except Exception:
            if self._consumer:
                raise
            self._logged = True
            logger.exception(FAILED)
        finally:
            self._close()
            await self._join()

    async def _join(self) -> None:
        """Wait for the hops and the sync batch calls to end, cancelled or not."""
        cancelled = await _outlast(self._hopping)
        scope = self._runtime.scope if self._opened else None
        while scope is not None:
            calls = drain_loaders(scope)
            if not calls:
                break
            cancelled = await _outlast(calls) or cancelled
        if cancelled:
            raise asyncio.CancelledError

    def _collect(self) -> bool:
        received = False
        while not self._queue.empty():
            received = True
            self._receive(self._queue.get_nowait())
        return received

    async def _next_event(self) -> None:
        capacity = self._executor.capacity()
        self._take(self._machine.capacity(capacity, time.monotonic()))
        if self._emits or self._finish is not None:
            return
        overdue = self._steal_overdue()
        if overdue is None:
            self._receive(await self._queue.get())
            return
        try:
            async with asyncio.timeout(overdue):
                message = await self._queue.get()
        except TimeoutError:
            return
        self._receive(message)

    def _steal_overdue(self) -> float | None:
        """Take own pool units queued past `QUEUE_TIMEOUT` onto the request thread.

        Each needs a free request permit, asked for again a `QUEUE_TIMEOUT` later
        when none is free. It returns the seconds to the next check, `None` for none.
        """
        now = time.monotonic()
        timeout = self._queue_timeout
        queued = self._queued
        checks: list[float] = []
        for _ in range(len(queued)):
            run = queued.popleft()
            work = cast("StealableTask[None]", run.work)
            if run.abandoned or work.future.running() or work.future.done():
                continue
            due = run.queued + timeout
            if now < due:
                checks.append(due - now)
            elif self._permits.take():
                stealing = self._hop_task(self._steal_hop(work))
                stealing.add_done_callback(self._unpermit)
                continue
            else:
                checks.append(max(timeout, COALESCE))
            queued.append(run)
        return min(checks, default=None)

    async def _steal_hop(self, work: StealableTask[None]) -> None:
        """Run a queued pool unit on the request thread under a taken permit."""
        steal = sync_to_async(
            self._steal_here, thread_sensitive=True, context=self._restored()
        )
        await self._hopped(steal(work))

    def _restored(self) -> contextvars.Context:
        """Return a copy of the snapshot, which a hop restores on the request thread."""
        return self._runtime.snapshot.context.copy()

    def _keep(self) -> None:
        """Keep the snapshot the hop restored here, so its jobs need no hop."""
        snapshot = self._runtime.snapshot
        if snapshot.get() is None:
            snapshot.keep(contextvars.copy_context())

    def _unpermit(self, _task: asyncio.Task[None]) -> None:
        self._permits.release()

    def _steal_here(self, work: StealableTask[None]) -> None:
        self._request_thread = threading.get_ident()
        self._keep()
        self._executor.steal(work)

    def _hop_task(self, hop: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        batch = self._loop.create_task(hop)
        self._batches.add(batch)
        batch.add_done_callback(self._batches.discard)
        return batch

    async def _hopped(self, hop: Awaitable[None]) -> None:
        """Await a hop to the request thread, marked so the response waits for it."""
        current = cast("asyncio.Task[None]", asyncio.current_task())
        self._hopping.add(current)
        try:
            await hop
        finally:
            self._hopping.discard(current)

    @override
    def _dispatch(self, run: Run) -> StealableTask[None]:
        """Queue the unit through asgiref, which restores the snapshot on its thread.

        The hop starts at once, so the executor takes or refuses the unit right here.
        """
        lane = self._lane
        if lane is None:
            lane = self._lane = Handoff(self._executor.submit)
        task, work = lane.start(
            partial(self._landed_body, run), self._restored(), self._loop
        )
        self._dispatched.add(task)
        task.add_done_callback(self._dispatched.discard)
        task.add_done_callback(partial(self._pool_done, run))
        return work

    def _landed_body(self, run: Run) -> None:
        """Keep the snapshot asgiref restored on this thread, then run the unit."""
        self._keep()
        self._pool_body(run)

    @override
    def _post(self, message: Message) -> None:
        if threading.get_ident() == self._loop_thread:
            self._queue.put_nowait(message)
            return
        with suppress(RuntimeError):
            self._loop.call_soon_threadsafe(self._queue.put_nowait, message)

    @override
    def _watch(self, run: Run, work: StealableTask[None]) -> None:
        self._queued.append(run)

    def _pool_done(self, run: Run, future: asyncio.Future[None]) -> None:
        self._post(Closed(run, error_of(future)))

    @override
    def _request(self, units: tuple[Unit, ...]) -> None:
        runs = [self._register(unit, Lane.REQUEST) for unit in units]
        self._hop_task(self._request_lane(runs))

    async def _request_lane(self, runs: list[Run]) -> None:
        """Run request-lane units in one hop under a permit, refusing chosen ones."""
        permits = self._permits
        if not permits.take():
            runs = self._refuse_unforced(runs)
            if not runs:
                return
            until = max(run.counted.until for run in runs)
            if not await permits.wait(until):
                self._expire(runs)
                return
        hop = sync_to_async(self._hop, thread_sensitive=True, context=self._restored())
        try:
            await self._hopped(hop(runs))
        finally:
            permits.release()

    def _refuse_unforced(self, runs: list[Run]) -> list[Run]:
        """Send the units no rule forced back to the pool, keeping the forced ones."""
        forced: list[Run] = []
        for run in runs:
            if run.unit.placement.forced:
                forced.append(run)
            else:
                self._shut(run)
                self._post(Refused(run.unit.id))
        return forced

    def _expire(self, runs: list[Run]) -> None:
        for run in runs:
            unit = run.unit
            for task in unit.tasks:
                self._post(runner.skipped(unit.id, task, unit.placement))
            self._post(Closed(run, None))

    def _hop(self, runs: list[Run]) -> None:
        """Run request-lane units in order on the request thread, each counted alone.

        A unit counts only once it starts, as the units behind it wait for its end.
        """
        self._request_thread = threading.get_ident()
        self._keep()
        for run in runs:
            with run.counted:
                self._run_hopped(run)
            self._post(Closed(run, None))

    def _run_hopped(self, run: Run) -> None:
        """Run the jobs of one request-lane unit until it is abandoned."""
        unit = run.unit
        for task in unit.tasks:
            if run.abandoned:
                return
            self._post(
                runner.run(
                    self._runtime,
                    unit.id,
                    task,
                    unit.placement,
                    lane=Lane.REQUEST,
                    queued=run.queued,
                    count=run.counted.job,
                )
            )

    @override
    def _wake(self, at: float) -> None:
        if self._timer is not None:
            self._timer.cancel()
        loop = self._loop
        when = loop.time() + max(0.0, at - time.monotonic())
        self._timer = loop.call_at(when, self._post, TICK)

    @override
    def _release(self) -> None:
        """Cancel the timer and every request task not yet running on the thread."""
        if self._timer is not None:
            self._timer.cancel()
        self._queued.clear()
        for batch in tuple(self._batches):
            if batch not in self._hopping:
                batch.cancel()


__all__ = ["AsyncDriver"]
