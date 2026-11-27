"""The bookkeeping both drivers share, every call to the machine made by the driver."""

import asyncio
import contextvars
import logging
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Final, Protocol, Self, assert_never

from django.conf import settings

from next.boundaries.options import BoundaryOptions
from next.bridge.context import ContextSnapshot
from next.bridge.tasks import StealableTask
from next.caches import FORK
from next.defer.coordinator import runner
from next.defer.coordinator.machine import (
    Abandon,
    Command,
    Emit,
    Finish,
    Machine,
    RunPool,
    RunRequest,
    Unit,
    Wake,
)
from next.defer.coordinator.runner import Report, Runtime
from next.defer.errors import BoundaryRefusedError, BoundaryShedError, ProtocolError
from next.defer.executors import BoundaryExecutor
from next.defer.ledger import ResponseJobs
from next.defer.manager import defer_manager
from next.defer.markers import Lane, LaneFacts, LaneRule, ServerKind, Timing
from next.defer.policy import InlineNotice
from next.deps.loader import CountedJob, count_job
from next.deps.scope import RequestScope


logger = logging.getLogger("next.defer")

COALESCE: Final = 0.002
"""The seconds a ready outcome waits for others to share its write."""

FAILED: Final = "The driver of a response's deferred jobs failed, so it stopped them."
_UNFINISHED: Final = "The lane ended the unit before this job reported."
_SERIAL: Final = (
    "The %d boundaries of this page ran one after another on the request thread, as "
    "the database is SQLite, so the page waited for their sum. Setting "
    "NEXT_FRAMEWORK['DEFER']['EXECUTOR']['SQLITE'] = \"parallel\" runs "
    "them on the pool for a file database, where a write waits for the lock another "
    "write holds."
)
_PLAIN: Final = BoundaryOptions()

serial_notice: Final = InlineNotice()
"""The once-per-process note of a page SQLite kept serial, shown under `DEBUG`."""

FORK.attach(serial_notice.forget)


def note_serial(facts: LaneFacts, jobs: int) -> None:
    """Explain once under `DEBUG` why SQLite ran the boundaries of a page one by one.

    An in-memory alias, an opt-out or a transaction stays quiet, as no setting cures it.
    """
    if not (jobs > 1 and facts.parallel and not facts.memory and settings.DEBUG):
        return
    if facts.rule(_PLAIN) is LaneRule.SQLITE and serial_notice.take(LaneRule.SQLITE):
        logger.warning(_SERIAL, jobs)


class Counted:
    """The count of a unit among the jobs that may add a key to a batch of the request.

    It counts once at most, so any path may end it and a start after the end is void.
    """

    __slots__ = ("_job", "_left", "scope", "until")

    def __init__(self, scope: RequestScope | None, until: float) -> None:
        """Count nothing yet, `until` bounding the count once it starts."""
        self.scope = scope
        self.until = until
        self._job: CountedJob | None = None
        self._left = False

    @property
    def job(self) -> CountedJob | None:
        """Return the job a body marks itself as, `None` while nothing counts."""
        return self._job

    def start(self) -> None:
        """Count the unit from now, unless it already counts or ended."""
        if self._job is None and not self._left:
            self._job = count_job(self.scope, self.until)

    def end(self) -> None:
        """Stop counting the unit, as it ended or will never start."""
        self._left = True
        job, self._job = self._job, None
        if job is not None:
            job.end()

    def ended(self, _future: Future[None]) -> None:
        """Stop counting a pool unit once its work is done, run, skipped or rejected."""
        self.end()

    def __enter__(self) -> Self:
        """Count the unit for the length of the block."""
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop counting the unit however the block ended."""
        self.end()


class Run:
    """One dispatched unit, the jobs it reported and whether the driver left it.

    Its jobs run one after another, so the unit counts as one until its last ends.
    """

    __slots__ = (
        "abandoned",
        "closed",
        "counted",
        "lane",
        "queued",
        "reported",
        "unit",
        "work",
    )

    def __init__(
        self, unit: Unit, lane: Lane, queued: float, scope: RequestScope | None
    ) -> None:
        """Hold `unit` dispatched on `lane` at the monotonic instant `queued`."""
        self.unit = unit
        self.lane = lane
        self.queued = queued
        self.abandoned = False
        self.closed = False
        self.reported: set[str] = set()
        self.work: StealableTask[None] | None = None
        self.counted = Counted(scope, max(task.deadline for task in unit.tasks))


@dataclass(frozen=True, slots=True)
class Closed:
    """A unit its lane ended, with what broke the lane if anything did."""

    run: Run
    error: BaseException | None


@dataclass(frozen=True, slots=True)
class Refused:
    """A request-lane unit sent back to the machine for another lane."""

    unit: str


@dataclass(frozen=True, slots=True)
class Idle:
    """A batch holding nothing, handed over once `idle` seconds passed without a write.

    Asking for the batch after it acknowledges nothing, as it carried no outcome.
    """

    items: tuple[()] = ()


IDLE: Final = Idle()


class Tick:
    """The wake a driver feeds itself at the instant the machine asked for."""

    __slots__ = ()


TICK: Final = Tick()

type Message = Report | Closed | Refused | Tick


def error_of(future: Future[Any] | asyncio.Future[Any]) -> BaseException | None:
    """Return what ended `future` other than a result, a cancel included."""
    if future.cancelled():
        return asyncio.CancelledError()
    return future.exception()


class Owner(Protocol):
    """The coordinator a driver reads its coalescing from and publishes its end to."""

    coalesce: float
    finish: Finish | None
    failed: bool


class Driver(ABC):
    """The bookkeeping both drivers share, every machine call made by the driver."""

    _executor: BoundaryExecutor
    _machine: Machine
    _runtime: Runtime

    def __init__(self, owner: Owner, session: ResponseJobs, server: ServerKind) -> None:
        """Hold the session, building nothing before the first batch is asked for."""
        self._owner = owner
        self._session = session
        self._server = server
        self._runs: dict[str, Run] = {}
        self._running: set[Run] = set()
        self._pending: deque[Command] = deque()
        self._emits: deque[Emit] = deque()
        self._finish: Finish | None = None
        self._request_thread: int | None = None
        self._taking = False
        self._opened = False
        self._consumer = False
        self._logged = False

    def _open(self) -> None:
        """Build the machine from the session and start it."""
        session = self._session
        facts = session.facts
        if facts is None:
            facts = LaneFacts(self._server)
        snapshot = session.snapshot
        if snapshot is None:
            snapshot = ContextSnapshot(contextvars.copy_context())
        note_serial(facts, len(session.jobs))
        self._executor = defer_manager.executor()
        self._runtime = Runtime(snapshot, session, facts, self._executor, session.scope)
        self._machine = Machine(
            session.plan(),
            session.caps,
            facts,
            defer_manager.admission(),
            deadline=session.deadline,
            coalesce=self._owner.coalesce,
        )
        self._opened = True
        self._take(self._machine.start(self._executor.capacity(), time.monotonic()))

    def _take(self, commands: list[Command]) -> None:
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

    def _hold(self, commands: list[Command]) -> Counted | None:
        """Count the start of several units as a job of its own while it runs.

        A unit started first may park on a key before the next one counts.
        """
        starts = sum(isinstance(command, RunPool) for command in commands)
        if starts > 1:
            hold = Counted(self._session.scope, self._session.deadline)
            hold.start()
            return hold
        return None

    def _command(self, command: Command) -> None:
        match command:
            case RunPool(unit=unit):
                self._pool(unit)
            case RunRequest(units=units):
                self._request(units)
            case Abandon(ids=ids):
                for unit_id in ids:
                    self._drop(self._runs[unit_id])
            case Emit():
                self._emits.append(command)
            case Wake(at=at):
                self._wake(at)
            case Finish():
                self._finish = command
            case _:
                assert_never(command)

    def _register(self, unit: Unit, lane: Lane) -> Run:
        run = Run(unit, lane, time.monotonic(), self._session.scope)
        self._runs[unit.id] = run
        self._running.add(run)
        return run

    def _shut(self, run: Run) -> None:
        """Mark a unit ended on its lane."""
        run.closed = True
        self._running.discard(run)

    def _pool(self, unit: Unit) -> None:
        """Submit a pool unit, which a full queue hands back to the machine.

        A unit a free worker takes counts from now, another once a thread starts it.
        """
        run = self._register(unit, Lane.POOL)
        capacity = self._executor.capacity()
        if capacity.queued < capacity.idle:
            run.counted.start()
        try:
            work = self._dispatch(run)
        except BoundaryShedError:
            run.counted.end()
            self._shut(run)
            self._take(self._machine.full(unit.id, time.monotonic()))
            return
        except BoundaryRefusedError as error:
            run.counted.end()
            self._refuse(run, error)
            return
        run.work = work
        work.future.add_done_callback(run.counted.ended)
        self._watch(run, work)

    def _pool_body(self, run: Run) -> None:
        """Run the jobs of a pool unit in order on the thread that claimed it."""
        here = threading.get_ident() == self._request_thread
        lane = Lane.REQUEST if here else Lane.POOL
        unit = run.unit
        counted = run.counted
        for task in unit.tasks:
            if run.abandoned:
                return
            counted.start()
            self._post(
                runner.run(
                    self._runtime,
                    unit.id,
                    task,
                    unit.placement,
                    lane=lane,
                    queued=run.queued,
                    count=counted.job,
                )
            )

    def _refuse(self, run: Run, error: BaseException) -> None:
        self._shut(run)
        unit = run.unit
        for task in unit.tasks:
            timing = Timing(placement=unit.placement)
            self._report(Report(unit.id, task.job.id, error, timing))

    def _receive(self, message: Message) -> None:
        match message:
            case Report():
                self._report(message)
            case Closed(run=run, error=error):
                self._closed(run, error)
            case Refused(unit=unit):
                self._take(self._machine.refused(unit, time.monotonic()))
            case Tick():
                self._tick()
            case _:
                assert_never(message)

    def _report(self, report: Report) -> None:
        """Hand one job's result to the machine, which drops it once the job settled."""
        self._runs[report.unit].reported.add(report.job)
        now = time.monotonic()
        self._take(
            self._machine.finished(report.job, report.result, report.timing, now)
        )

    def _closed(self, run: Run, error: BaseException | None) -> None:
        """Mark a unit ended and fail the jobs a broken lane never reported."""
        self._shut(run)
        missing = error or RuntimeError(_UNFINISHED)
        unit = run.unit
        for task in unit.tasks:
            if task.job.id not in run.reported:
                timing = Timing(placement=unit.placement)
                self._report(Report(unit.id, task.job.id, missing, timing))

    def _drop(self, run: Run) -> None:
        """Leave a unit, dropping its pool work no thread claimed yet."""
        if run.abandoned or run.closed:
            return
        run.abandoned = True
        self._running.discard(run)
        work = run.work
        if work is not None:
            self._executor.steal(work)

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
        self._owner.finish = self._finish
        self._owner.failed = self._logged

    def _cancel_machine(self) -> list[Command]:
        try:
            return self._machine.cancel(time.monotonic())
        except ProtocolError:
            if not self._logged:
                self._logged = True
                logger.exception(FAILED)
            return []

    @abstractmethod
    def _dispatch(self, run: Run) -> StealableTask[None]:
        """Queue the body of a pool unit on the executor, raising what refuses it."""

    @abstractmethod
    def _post(self, message: Message) -> None:
        """Hand `message` to the driving thread from any thread, never blocking."""

    @abstractmethod
    def _watch(self, run: Run, work: StealableTask[None]) -> None:
        """Learn when a submitted pool unit ends."""

    @abstractmethod
    def _request(self, units: tuple[Unit, ...]) -> None:
        """Run request-lane units in order on the request thread."""

    @abstractmethod
    def _wake(self, at: float) -> None:
        """Feed a tick at the monotonic instant `at`, replacing an earlier one."""

    @abstractmethod
    def _release(self) -> None:
        """Let go of the driver's own timers and waits."""


__all__ = [
    "COALESCE",
    "FAILED",
    "IDLE",
    "TICK",
    "Closed",
    "Counted",
    "Driver",
    "Idle",
    "Message",
    "Owner",
    "Refused",
    "Run",
    "Tick",
    "error_of",
    "note_serial",
    "serial_notice",
]
