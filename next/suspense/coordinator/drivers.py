"""The thread and event-loop drivers that run the commands of one response's machine."""

import asyncio
import contextvars
import logging
import math
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Coroutine, Generator, Iterable
from concurrent.futures import Future, InvalidStateError
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from queue import Empty, SimpleQueue
from typing import Any, Final, assert_never, cast, override

from asgiref.sync import AsyncToSync, SyncToAsync, sync_to_async
from django.core.exceptions import ImproperlyConfigured

from next.conf.defer import defer_count, defer_seconds
from next.deps.loader import CountedJob, count_job, drain_loaders
from next.suspense.coordinator import runner
from next.suspense.coordinator.machine import (
    Abandon,
    Admitter,
    Command,
    Emit,
    Finish,
    Machine,
    ProtocolError,
    RunPool,
    RunRequest,
    StartLoop,
    Totals,
    Unit,
    Wake,
)
from next.suspense.coordinator.placement import COSTS, Thresholds, place
from next.suspense.coordinator.runner import Report, Runtime
from next.suspense.errors import BoundaryRefusedError, BoundaryShedError
from next.suspense.executors import (
    BoundaryExecutor,
    RequestThreadExecutor,
    background_loop,
    check_asgiref,
    request_executor,
)
from next.suspense.manager import suspense_manager
from next.suspense.markers import (
    Capacity,
    ForegroundFacts,
    Job,
    Lane,
    Placement,
    Server,
    Timing,
)
from next.suspense.session import BoundarySession
from next.tasks import StealableTask
from next.utils import after_fork


logger = logging.getLogger("next.suspense")

COALESCE: Final = 0.002
"""The seconds a ready outcome waits for others to share its write."""

_FAILED: Final = "The driver of a response's deferred jobs failed, so it stopped them."
_UNFINISHED: Final = "The lane ended the unit before this job reported."

type _Waiter = tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]


class Foreground:
    """The process-wide permits bounding request-lane work under ASGI.

    A waiter awaits a future of its own loop, so no loop ever waits on the lock.
    """

    def __init__(self, limit: int) -> None:
        """Hold `limit` free permits and no waiter."""
        self.limit = limit
        self._lock = threading.Lock()
        self._free = limit
        self._waiters: deque[_Waiter] = deque()

    def take(self) -> bool:
        """Take a free permit at once, false when none is free or one is owed."""
        with self._lock:
            if self._free and not self._waiters:
                self._free -= 1
                return True
        return False

    async def wait(self, until: float) -> bool:
        """Take a permit before the monotonic instant `until`, false once it passed.

        One hold of the lock checks and queues, so no release strands a free permit.
        """
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._free and not self._waiters:
                self._free -= 1
                return True
            waiter: asyncio.Future[None] = loop.create_future()
            entry = (loop, waiter)
            self._waiters.append(entry)
        try:
            async with asyncio.timeout(max(0.0, until - time.monotonic())):
                await waiter
        except TimeoutError:
            self._leave(entry)
            return False
        except asyncio.CancelledError:
            self._leave(entry)
            raise
        return True

    def release(self) -> None:
        """Hand a permit to the oldest waiter whose loop still runs, or free it."""
        with self._lock:
            while self._waiters:
                loop, waiter = self._waiters.popleft()
                try:
                    loop.call_soon_threadsafe(self._grant, waiter)
                except RuntimeError:
                    continue
                return
            self._free += 1

    def _grant(self, waiter: asyncio.Future[None]) -> None:
        if waiter.done():
            self.release()
        else:
            waiter.set_result(None)

    def _leave(self, entry: _Waiter) -> None:
        """Drop a waiter that stopped waiting, handing back a permit it was granted."""
        with self._lock, suppress(ValueError):
            self._waiters.remove(entry)
        waiter = entry[1]
        if waiter.done() and not waiter.cancelled():
            self.release()


_permits: dict[int, Foreground] = {}
_permits_lock = threading.Lock()
after_fork(_permits.clear)


def foreground() -> Foreground:
    """Return the permits of this process, sized by `MAX_FOREGROUND`."""
    limit = defer_count("EXECUTOR", "OPTIONS", "MAX_FOREGROUND")
    held = _permits.get(limit)
    if held is None:
        with _permits_lock:
            held = _permits.setdefault(limit, Foreground(limit))
    return held


class _Placer:
    """The placement decision with the thresholds bound, as the machine calls it."""

    __slots__ = ("_options",)

    def __init__(self, options: Thresholds) -> None:
        self._options = options

    def __call__(
        self, job: Job[Any], facts: ForegroundFacts, *, alone: bool
    ) -> Placement:
        return place(job, facts, self._options, COSTS, alone=alone)


class _Run[D]:
    """One dispatched unit, the jobs it reported and whether the driver left it."""

    __slots__ = (
        "abandoned",
        "closed",
        "count",
        "lane",
        "queued",
        "reported",
        "task",
        "unit",
        "work",
    )

    def __init__(self, unit: Unit[D], lane: Lane, queued: float) -> None:
        self.unit = unit
        self.lane = lane
        self.queued = queued
        self.abandoned = False
        self.closed = False
        self.reported: set[str] = set()
        self.work: StealableTask[None] | None = None
        self.task: asyncio.Task[None] | None = None
        self.count: CountedJob | None = None


@dataclass(frozen=True, slots=True)
class _Closed:
    run: _Run[Any]
    error: BaseException | None


@dataclass(frozen=True, slots=True)
class _Refused:
    unit: str


@dataclass(frozen=True, slots=True)
class Idle:
    """A batch holding nothing, handed over once `idle` seconds passed without a write.

    Asking for the batch after it acknowledges nothing, as it carried no outcome.
    """

    items: tuple[()] = ()


IDLE: Final = Idle()


class _Tick:
    __slots__ = ()


_TICK: Final = _Tick()

type _Message = Report[Any] | _Closed | _Refused | _Tick


def _error_of(future: Future[Any] | asyncio.Future[Any]) -> BaseException | None:
    if future.cancelled():
        return asyncio.CancelledError()
    return future.exception()


def _uncount(run: _Run[Any]) -> None:
    """Stop counting a unit that ended or will never start, once."""
    count, run.count = run.count, None
    if count is not None:
        count.end()


def _uncounted(run: _Run[Any], _future: Future[None]) -> None:
    """Stop counting a pool unit once its work is done, run, skipped or rejected."""
    _uncount(run)


def _ring(signal: Future[None]) -> None:
    """Complete `signal` unless done, from any thread."""
    if not signal.done():
        with suppress(InvalidStateError):
            signal.set_result(None)


class Coordinator[D]:
    """Run the deferred jobs of one response, handing its outcomes over batch by batch.

    Asking for the next batch acknowledges the last one as written, so a consumer that
    stops early leaves it unwritten and cancels what still runs.
    """

    def __init__(
        self,
        *,
        executor: BoundaryExecutor | None = None,
        admission: Admitter | None = None,
        thresholds: Thresholds | None = None,
        permits: Foreground | None = None,
        coalesce: float = COALESCE,
    ) -> None:
        """Use the process backends and settings for whatever is not given."""
        self.executor = executor
        self.admission = admission
        self.thresholds = thresholds
        self.permits = permits
        self.coalesce = coalesce
        self.finish: Finish | None = None
        self.timings: dict[str, Timing] = {}
        self.failed = False

    @property
    def totals(self) -> Totals | None:
        """Return the counts of the last response, `None` before it finished."""
        return None if self.finish is None else self.finish.totals

    def run(
        self,
        session: BoundarySession[D],
        *,
        idle: float | None = None,
        buffered: bool = False,
    ) -> Generator[Emit[D] | Idle, None, None]:
        """Yield the batches on the calling thread, a WSGI worker or a plain thread.

        With `idle` set, `IDLE` follows each quiet `idle` seconds, so a writer can ping.
        `buffered` says the consumer writes nothing before the last batch.
        """
        self.finish = None
        self.timings = {}
        self.failed = False
        return ThreadDriver(self, session, idle=idle, buffered=buffered).run()

    def arun(self, session: BoundarySession[D]) -> AsyncGenerator[Emit[D], None]:
        """Yield the batches on the running server loop."""
        self.finish = None
        self.timings = {}
        self.failed = False
        return AsyncDriver(self, session).run()


class _Driver[D](ABC):
    """The bookkeeping both drivers share, every machine call made by the driver."""

    _executor: BoundaryExecutor
    _machine: Machine[D]
    _runtime: Runtime

    def __init__(
        self, coordinator: Coordinator[D], session: BoundarySession[D], server: Server
    ) -> None:
        """Hold the session, building nothing before the first batch is asked for."""
        self._coordinator = coordinator
        self._session = session
        self._server = server
        self._runs: dict[str, _Run[D]] = {}
        self._running: set[_Run[D]] = set()
        self._pending: deque[Command[D]] = deque()
        self._emits: deque[Emit[D]] = deque()
        self._timings: dict[str, Timing] = {}
        self._finish: Finish | None = None
        self._request_thread: int | None = None
        self._taking = False
        self._opened = False
        self._consumer = False
        self._logged = False

    def _open(self, hops: RequestThreadExecutor | None) -> None:
        """Build the machine from the session and start it."""
        coordinator = self._coordinator
        session = self._session
        facts = session.facts
        if facts is None:
            facts = ForegroundFacts(self._server)
        snapshot = session.snapshot
        if snapshot is None:
            snapshot = contextvars.copy_context()
        self._executor = coordinator.executor or suspense_manager.executor()
        self._runtime = Runtime(
            snapshot, session, facts, self._executor, COSTS, session.scope, hops
        )
        self._machine = Machine(
            session.plan(),
            session.caps,
            facts,
            _Placer(coordinator.thresholds or Thresholds.from_settings()),
            coordinator.admission or suspense_manager.admission(),
            deadline=session.deadline,
            coalesce=coordinator.coalesce,
        )
        self._opened = True
        self._take(self._machine.start(self._executor.capacity(), time.monotonic()))

    def _take(self, commands: list[Command[D]]) -> None:
        """Run `commands` in order, queueing those a command produces behind them."""
        self._pending.extend(commands)
        if self._taking:
            return
        self._taking = True
        hold = self._hold(commands)
        try:
            while self._pending and self._finish is None:
                self._command(self._pending.popleft())
        finally:
            self._taking = False
            if hold is not None:
                hold.end()

    def _hold(self, commands: list[Command[D]]) -> CountedJob | None:
        """Count the start of several units as a job of its own while it runs.

        A unit started first may park on a key before the next one counts.
        """
        starts = sum(isinstance(command, RunPool | StartLoop) for command in commands)
        if starts > 1:
            return count_job(self._runtime.scope, self._session.deadline)
        return None

    def _command(self, command: Command[D]) -> None:
        match command:
            case RunPool(unit=unit):
                self._pool(unit)
            case RunRequest(units=units):
                self._request(units)
            case StartLoop(unit=unit):
                self._start(unit)
            case Abandon(ids=ids):
                for unit_id in ids:
                    self._drop(self._runs[unit_id])
            case Emit(items=items):
                for item in items:
                    self._timings.setdefault(item.id, item.timing)
                self._emits.append(command)
            case Wake(at=at):
                self._wake(at)
            case Finish():
                self._finish = command
            case _:
                assert_never(command)

    def _register(self, unit: Unit[D], lane: Lane) -> _Run[D]:
        run = _Run(unit, lane, time.monotonic())
        self._runs[unit.id] = run
        self._running.add(run)
        return run

    def _shut(self, run: _Run[D]) -> None:
        """Mark a unit ended on its lane."""
        run.closed = True
        self._running.discard(run)

    def _count(self, unit: Unit[D]) -> CountedJob | None:
        """Count a unit among the jobs that may add a key to a batch of the request.

        Its jobs run one after another, so the unit counts as one until its last ends.
        """
        until = max(task.deadline for task in unit.tasks)
        return count_job(self._runtime.scope, until)

    def _pool(self, unit: Unit[D]) -> None:
        """Submit a pool unit, which a full queue hands back to the machine.

        A unit a free worker takes counts from now, another once a thread starts it.
        """
        run = self._register(unit, Lane.POOL)
        work = StealableTask(partial(self._pool_body, run))
        run.work = work
        capacity = self._executor.capacity()
        if capacity.queued < capacity.idle:
            run.count = self._count(unit)
        try:
            self._executor.submit(work)
        except BoundaryShedError:
            _uncount(run)
            self._shut(run)
            self._take(self._machine.full(unit.id, time.monotonic()))
            return
        except BoundaryRefusedError as error:
            _uncount(run)
            self._refuse(run, error)
            return
        work.future.add_done_callback(partial(_uncounted, run))
        self._watch(run, work)

    def _pool_body(self, run: _Run[D]) -> None:
        """Run the jobs of a pool unit in order on the thread that claimed it."""
        here = threading.get_ident() == self._request_thread
        lane = Lane.REQUEST if here else Lane.POOL
        unit = run.unit
        if run.count is None:
            run.count = self._count(unit)
        for task in unit.tasks:
            if run.abandoned:
                return
            self._post(
                runner.run(
                    self._runtime,
                    unit.id,
                    task,
                    unit.placement,
                    lane=lane,
                    queued=run.queued,
                    count=run.count,
                )
            )

    async def _loop_body(self, run: _Run[D]) -> None:
        """Run the jobs of a loop unit in order, each reported as it finishes."""
        unit = run.unit
        for task in unit.tasks:
            self._post(
                await runner.arun(
                    self._runtime,
                    unit.id,
                    task,
                    unit.placement,
                    queued=run.queued,
                    count=run.count,
                )
            )

    def _loop_done(self, run: _Run[D], task: asyncio.Task[None]) -> None:
        _uncount(run)
        self._post(_Closed(run, _error_of(task)))

    def _refuse(self, run: _Run[D], error: BaseException) -> None:
        self._shut(run)
        unit = run.unit
        for task in unit.tasks:
            timing = Timing(placement=unit.placement)
            self._report(Report(unit.id, task.job.id, error, timing))

    def _receive(self, message: _Message) -> None:
        match message:
            case Report():
                self._report(message)
            case _Closed(run=run, error=error):
                self._closed(run, error)
            case _Refused(unit=unit):
                self._take(self._machine.refused(unit, time.monotonic()))
            case _Tick():
                self._tick()
            case _:
                assert_never(message)

    def _report(self, report: Report[D]) -> None:
        """Hand one job's result to the machine, which drops it once the job settled."""
        self._runs[report.unit].reported.add(report.job)
        self._timings[report.job] = report.timing
        now = time.monotonic()
        self._take(
            self._machine.finished(report.job, report.result, report.timing, now)
        )

    def _closed(self, run: _Run[D], error: BaseException | None) -> None:
        """Mark a unit ended and fail the jobs a broken lane never reported."""
        self._shut(run)
        missing = error or RuntimeError(_UNFINISHED)
        unit = run.unit
        for task in unit.tasks:
            if task.job.id not in run.reported:
                timing = Timing(placement=unit.placement)
                self._report(Report(unit.id, task.job.id, missing, timing))

    def _drop(self, run: _Run[D]) -> None:
        """Leave a unit, dropping queued pool work and cancelling a loop task."""
        if run.abandoned or run.closed:
            return
        run.abandoned = True
        self._running.discard(run)
        work = run.work
        if work is not None:
            self._executor.steal(work)
        if run.lane is Lane.LOOP:
            self._cancel(run)

    def _acknowledge(self) -> None:
        self._take(self._machine.written(time.monotonic()))

    def _tick(self) -> None:
        self._take(self._machine.tick(time.monotonic()))

    def _close(self) -> None:
        """Cancel what still runs, never waiting, and publish the accounting."""
        if self._opened:
            self._pending.clear()
            if not self._machine.closed:
                self._take(self._cancel_machine())
            for run in tuple(self._running):
                self._drop(run)
            self._release()
            if self._finish is None:
                self._finish = Finish(
                    aborted=True, cut=False, totals=self._machine.totals
                )
        self._coordinator.finish = self._finish
        self._coordinator.timings = dict(self._timings)
        self._coordinator.failed = self._logged

    def _cancel_machine(self) -> list[Command[D]]:
        try:
            return self._machine.cancel(time.monotonic())
        except (ProtocolError, AssertionError):
            if not self._logged:
                self._logged = True
                logger.exception(_FAILED)
            return []

    @abstractmethod
    def _post(self, message: _Message) -> None:
        """Hand `message` to the driving thread from any thread, never blocking."""

    @abstractmethod
    def _watch(self, run: _Run[D], work: StealableTask[None]) -> None:
        """Learn when a submitted pool unit ends."""

    @abstractmethod
    def _request(self, units: tuple[Unit[D], ...]) -> None:
        """Run request-lane units in order on the request thread."""

    @abstractmethod
    def _start(self, unit: Unit[D]) -> None:
        """Start a unit of async jobs on the loop lane."""

    @abstractmethod
    def _cancel(self, run: _Run[D]) -> None:
        """Cancel the task of a loop unit."""

    @abstractmethod
    def _wake(self, at: float) -> None:
        """Feed a tick at the monotonic instant `at`, replacing an earlier one."""

    @abstractmethod
    def _release(self) -> None:
        """Let go of the driver's own timers and waits."""


class ThreadDriver[D](_Driver[D]):
    """Drive one response from its request thread, inside the body generator.

    With nothing to write the thread runs request-lane units, serves queued hops, steals
    its own queued pool units and then waits, serving hops until the next event. Unless
    buffered it steals only while no worker is free and none of its own units runs.
    """

    _hops: RequestThreadExecutor

    def __init__(
        self,
        coordinator: Coordinator[D],
        session: BoundarySession[D],
        *,
        idle: float | None = None,
        buffered: bool = False,
    ) -> None:
        """Open the queue every lane reports into, read by the request thread only."""
        super().__init__(coordinator, session, Server.WSGI_THREADED)
        self._buffered = buffered
        self._inbox: SimpleQueue[_Message] = SimpleQueue()
        self._signal: Future[None] | None = None
        self._backlog: deque[_Run[D]] = deque()
        self._stealable: deque[StealableTask[None]] = deque()
        self._background: asyncio.AbstractEventLoop | None = None
        self._wake_at = session.deadline + session.caps.write_grace
        self._idle = math.inf if idle is None else idle
        self._idle_at = math.inf

    def run(self) -> Generator[Emit[D] | Idle, None, None]:
        """Yield the batches, each request for the next one acknowledging the last.

        An `IDLE` comes only after the batch before it was acknowledged, holding none.
        """
        self._request_thread = threading.get_ident()
        with request_executor() as hops:
            self._hops = hops
            try:
                self._open(hops)
                self._idle_at = time.monotonic() + self._idle
                while self._finish is None:
                    batch: Emit[D] | Idle
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
                logger.exception(_FAILED)
            finally:
                self._close()

    def _step(self) -> None:
        """Take what arrived, else run, else serve hops and steal, else wait."""
        if self._collect():
            return
        if self._backlog:
            self._run_backlog()
            return
        capacity = self._executor.capacity()
        self._take(self._machine.capacity(capacity, time.monotonic()))
        if self._emits or self._backlog or self._finish is not None:
            return
        self._serve_queued()
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
        run.count = self._count(unit)
        try:
            for task in unit.tasks:
                self._report(
                    runner.run(
                        self._runtime,
                        unit.id,
                        task,
                        unit.placement,
                        lane=Lane.REQUEST,
                        queued=run.queued,
                        count=run.count,
                    )
                )
        finally:
            _uncount(run)
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
        """Whether a unit of this response runs on a worker or the loop right now."""
        for run in self._running:
            if run.lane is Lane.LOOP:
                return True
            work = run.work
            if work is not None and work.future.running():
                return True
        return False

    def _block(self) -> None:
        """Wait for a report, the next wake or the idle instant, serving any hops."""
        until = min(self._wake_at, self._idle_at)
        if self._background is None:
            self._wait(until)
        else:
            self._serve(until)
        if time.monotonic() >= self._wake_at:
            self._tick()

    def _wait(self, until: float) -> None:
        try:
            message = self._inbox.get(timeout=max(0.0, until - time.monotonic()))
        except Empty:
            return
        self._receive(message)

    def _serve(self, until: float) -> None:
        """Serve hops until a report rings the signal or the wake instant comes."""
        signal: Future[None] = Future()
        hops = self._hops
        signal.add_done_callback(lambda _: hops.wake())
        self._signal = signal
        try:
            if self._inbox.empty():
                hops.serve(signal, max(0.0, until - time.monotonic()))
        finally:
            self._signal = None

    def _serve_queued(self) -> None:
        """Run the hops already queued, so they go before any stolen pool work."""
        if self._background is not None:
            self._hops.serve(Future(), 0.0)

    @override
    def _post(self, message: _Message) -> None:
        self._inbox.put(message)
        signal = self._signal
        if signal is not None:
            _ring(signal)

    @override
    def _watch(self, run: _Run[D], work: StealableTask[None]) -> None:
        self._stealable.append(work)
        work.future.add_done_callback(partial(self._pool_done, run))

    def _pool_done(self, run: _Run[D], future: Future[None]) -> None:
        self._post(_Closed(run, _error_of(future)))

    @override
    def _request(self, units: tuple[Unit[D], ...]) -> None:
        for unit in units:
            self._backlog.append(self._register(unit, Lane.REQUEST))

    @override
    def _start(self, unit: Unit[D]) -> None:
        run = self._register(unit, Lane.LOOP)
        try:
            check_asgiref()
        except ImproperlyConfigured as error:
            self._refuse(run, error)
            return
        run.count = self._count(unit)
        self._background = background_loop.call_soon(self._spawn, run)

    def _spawn(self, run: _Run[D]) -> None:
        """Create the task of a loop unit, called on the background loop."""
        loop = asyncio.get_running_loop()
        task = loop.create_task(self._loop_body(run), context=contextvars.Context())
        run.task = task
        task.add_done_callback(partial(self._loop_done, run))

    @override
    def _cancel(self, run: _Run[D]) -> None:
        background_loop.call_soon(_cancel_task, run)

    @override
    def _wake(self, at: float) -> None:
        self._wake_at = at

    @override
    def _release(self) -> None:
        self._backlog.clear()
        self._stealable.clear()


def _idle() -> None:
    """Do nothing, as a hop that returns only once the hops queued before it ran."""


async def _cleared() -> None:
    """Return once the request thread of the current context ran its queued hops."""
    AsyncToSync.executors.current = None
    await SyncToAsync(_idle, thread_sensitive=True)()


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


def _cancel_task(run: _Run[Any]) -> None:
    """Cancel the task of a loop unit, which its spawn set before this call ran."""
    cast("asyncio.Task[None]", run.task).cancel()


class AsyncDriver[D](_Driver[D]):
    """Drive one response on the server loop, inside the async body iterator.

    Every wait is an await, and the request-lane units of a command share one hop. A
    response cannot end before its running request-lane job returns, as a sync job
    cannot be interrupted and the request thread must be free when the request ends.
    """

    _loop: asyncio.AbstractEventLoop
    _permits: Foreground

    def __init__(
        self, coordinator: Coordinator[D], session: BoundarySession[D]
    ) -> None:
        """Hold no loop until the first batch is asked for."""
        super().__init__(coordinator, session, Server.ASGI)
        self._queue: asyncio.Queue[_Message] = asyncio.Queue()
        self._loop_thread: int | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._batches: set[asyncio.Task[None]] = set()
        self._hopping: set[asyncio.Task[None]] = set()
        self._queued: deque[_Run[D]] = deque()
        self._queue_timeout = defer_seconds("EXECUTOR", "OPTIONS", "QUEUE_TIMEOUT")

    async def run(self) -> AsyncGenerator[Emit[D], None]:
        """Yield the batches, each request for the next one acknowledging the last."""
        self._loop = asyncio.get_running_loop()
        self._loop_thread = threading.get_ident()
        try:
            self._permits = self._coordinator.permits or foreground()
            self._open(None)
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
            logger.exception(_FAILED)
        finally:
            self._close()
            await self._join()

    async def _join(self) -> None:
        """Wait for the hops, loop jobs and sync batch calls to end, cancelled or not.

        A cancelled job leaves its hop running, and one queued after it proves it ended.
        """
        jobs = {run.task for run in self._runs.values() if run.task is not None}
        cancelled = await _outlast({*self._hopping, *jobs})
        snapshot = self._runtime.snapshot if jobs else None
        sensitive = SyncToAsync.thread_sensitive_context
        if snapshot is not None and snapshot.get(sensitive) is not None:
            cleared = self._loop.create_task(_cleared(), context=snapshot.copy())
            cancelled = await _outlast({cleared}) or cancelled
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

        Each needs a free foreground permit, asked for again a `QUEUE_TIMEOUT` later
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
        steal = sync_to_async(self._steal_here, thread_sensitive=True)
        await self._hopped(steal(work))

    def _unpermit(self, _task: asyncio.Task[None]) -> None:
        self._permits.release()

    def _steal_here(self, work: StealableTask[None]) -> None:
        self._request_thread = threading.get_ident()
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
    def _post(self, message: _Message) -> None:
        if threading.get_ident() == self._loop_thread:
            self._queue.put_nowait(message)
            return
        with suppress(RuntimeError):
            self._loop.call_soon_threadsafe(self._queue.put_nowait, message)

    @override
    def _watch(self, run: _Run[D], work: StealableTask[None]) -> None:
        self._queued.append(run)
        future = asyncio.wrap_future(work.future, loop=self._loop)
        future.add_done_callback(partial(self._pool_done, run))

    def _pool_done(self, run: _Run[D], future: asyncio.Future[None]) -> None:
        self._post(_Closed(run, _error_of(future)))

    @override
    def _request(self, units: tuple[Unit[D], ...]) -> None:
        runs = [self._register(unit, Lane.REQUEST) for unit in units]
        self._hop_task(self._foreground(runs))

    async def _foreground(self, runs: list[_Run[D]]) -> None:
        """Run request-lane units in one hop under a permit, refusing chosen ones."""
        permits = self._permits
        if not permits.take():
            runs = self._refuse_chosen(runs)
            if not runs:
                return
            until = max(task.deadline for run in runs for task in run.unit.tasks)
            if not await permits.wait(until):
                self._expire(runs)
                return
        try:
            await self._hopped(sync_to_async(self._hop, thread_sensitive=True)(runs))
        finally:
            permits.release()

    def _refuse_chosen(self, runs: list[_Run[D]]) -> list[_Run[D]]:
        """Send the units placement chose back to the pool, keeping the forced ones."""
        forced: list[_Run[D]] = []
        for run in runs:
            if run.unit.placement.forced:
                forced.append(run)
            else:
                self._shut(run)
                self._post(_Refused(run.unit.id))
        return forced

    def _expire(self, runs: list[_Run[D]]) -> None:
        for run in runs:
            unit = run.unit
            for task in unit.tasks:
                self._post(runner.skipped(unit.id, task, unit.placement))
            self._post(_Closed(run, None))

    def _hop(self, runs: list[_Run[D]]) -> None:
        """Run request-lane units in order on the request thread, each counted alone.

        A unit counts only once it starts, as the units behind it wait for its end.
        """
        self._request_thread = threading.get_ident()
        for run in runs:
            unit = run.unit
            run.count = self._count(unit)
            try:
                self._run_hopped(run)
            finally:
                _uncount(run)
            self._post(_Closed(run, None))

    def _run_hopped(self, run: _Run[D]) -> None:
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
                    count=run.count,
                )
            )

    @override
    def _start(self, unit: Unit[D]) -> None:
        run = self._register(unit, Lane.LOOP)
        run.count = self._count(unit)
        task = self._loop.create_task(
            self._loop_body(run), context=contextvars.Context()
        )
        run.task = task
        task.add_done_callback(partial(self._loop_done, run))

    @override
    def _cancel(self, run: _Run[D]) -> None:
        cast("asyncio.Task[None]", run.task).cancel()

    @override
    def _wake(self, at: float) -> None:
        if self._timer is not None:
            self._timer.cancel()
        loop = self._loop
        when = loop.time() + max(0.0, at - time.monotonic())
        self._timer = loop.call_at(when, self._post, _TICK)

    @override
    def _release(self) -> None:
        """Cancel the timer and every foreground task not yet running on the thread."""
        if self._timer is not None:
            self._timer.cancel()
        self._queued.clear()
        for batch in tuple(self._batches):
            if batch not in self._hopping:
                batch.cancel()


__all__ = [
    "COALESCE",
    "AsyncDriver",
    "Coordinator",
    "Foreground",
    "ThreadDriver",
    "foreground",
]
