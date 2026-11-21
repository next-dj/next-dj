"""The state machine of one response, fed timed events and answering with commands."""

from __future__ import annotations

import bisect
import enum
import heapq
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Protocol

from next.defer import ErrorKind
from next.suspense.errors import BoundaryRefusedError
from next.suspense.markers import (
    Admit,
    Capacity,
    Group,
    Job,
    Lane,
    Outcome,
    Placement,
    Server,
    Timing,
    Verdict,
)


if TYPE_CHECKING:
    from next.suspense.markers import Caps, ForegroundFacts, Plan, Rendered


_SETTLED_TWICE: Final = "a job reached a second terminal state"
_COMMANDED_TWICE: Final = "a job was commanded twice"
_EMITTED_TWICE: Final = "an outcome was emitted twice"
_AFTER_FINISH: Final = "a command followed Finish"


class ProtocolError(RuntimeError):
    """Raised when a driver breaks the event protocol or the machine its invariants."""


class Placer(Protocol):
    """The placement decision with the options and the cost table already bound."""

    def __call__(
        self, job: Job[Any], facts: ForegroundFacts, *, alone: bool
    ) -> Placement:
        """Return where the sync work of `job` runs and why."""


class Admitter(Protocol):
    """The admission backend, consulted for every pool-placed unit before it runs."""

    def admit(self, capacity: Capacity, *, forced: bool) -> Admit:
        """Return whether the unit goes to the pool, the request thread or is shed."""


class End(enum.StrEnum):
    """The one terminal state every job of a response reaches."""

    RAN = "ran"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SHED = "shed"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"


@dataclass(frozen=True, slots=True)
class Task[D]:
    """One job a lane runs, and the instant past which it no longer starts."""

    job: Job[D]
    deadline: float


@dataclass(frozen=True, slots=True)
class Unit[D]:
    """Jobs a lane runs one after another, a lone job or the members of a group."""

    id: str
    tasks: tuple[Task[D], ...]
    placement: Placement


@dataclass(frozen=True, slots=True)
class RunPool[D]:
    """Submit one unit to the pool lane."""

    unit: Unit[D]


@dataclass(frozen=True, slots=True)
class RunRequest[D]:
    """Run units on the request thread in order, as one hop under ASGI."""

    units: tuple[Unit[D], ...]


@dataclass(frozen=True, slots=True)
class StartLoop[D]:
    """Start one unit of `async def` jobs on the loop lane."""

    unit: Unit[D]


@dataclass(frozen=True, slots=True)
class Abandon:
    """Stop waiting for units, cancelling loop tasks and unclaimed pool work."""

    ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Settlement[D]:
    """A job settled as an error without a result, the writer rendering its `fallback`.

    `error` holds what the lane raised, `None` for a job the machine settled itself.
    """

    job: Job[D]
    kind: ErrorKind
    timing: Timing = field(default_factory=Timing)
    error: BaseException | None = None

    @property
    def id(self) -> str:
        """Return the wire id of the settled job."""
        return self.job.id


@dataclass(frozen=True, slots=True)
class Emit[D]:
    """Write these outcomes in document order as one write with one flush."""

    items: tuple[Outcome[D] | Settlement[D], ...]
    first: bool


@dataclass(frozen=True, slots=True)
class Wake:
    """Feed a `tick` at the monotonic instant `at`, replacing any earlier request."""

    at: float


@dataclass(frozen=True, slots=True)
class Totals:
    """What the signals report about one response, every count in jobs."""

    late: int = 0
    shed: int = 0
    timeouts: int = 0
    abandoned: int = 0
    cancelled: int = 0
    dropped: int = 0


@dataclass(frozen=True, slots=True)
class Finish:
    """The response is over, `aborted` when the client left, `cut` past the grace."""

    aborted: bool
    cut: bool
    totals: Totals


type Command[D] = (
    RunPool[D] | RunRequest[D] | StartLoop[D] | Abandon | Emit[D] | Wake | Finish
)


@dataclass(slots=True, eq=False)
class _Entry[D]:
    job: Job[D]
    ordinal: tuple[int, ...]
    deadline: float
    unit: _Unit[D] | None = None
    end: End | None = None
    item: Outcome[D] | Settlement[D] | None = None
    commanded: bool = False
    reported: bool = False
    credited: bool = False


@dataclass(slots=True, eq=False)
class _Unit[D]:
    id: str
    entries: tuple[_Entry[D], ...]
    placement: Placement
    lane: Lane
    grouped: bool = False
    dispatched: bool = False
    abandoned: bool = False
    keep_off_request: bool = False
    open: int = 0

    @property
    def ordinal(self) -> tuple[int, ...]:
        return self.entries[0].ordinal

    def live(self) -> list[_Entry[D]]:
        return [entry for entry in self.entries if entry.end is None]


def _ordinal[D](unit: _Unit[D]) -> tuple[int, ...]:
    return unit.ordinal


@dataclass(slots=True)
class _Counts:
    late: int = 0
    shed: int = 0
    timeouts: int = 0
    abandoned: int = 0
    cancelled: int = 0
    dropped: int = 0

    def frozen(self) -> Totals:
        return Totals(
            self.late,
            self.shed,
            self.timeouts,
            self.abandoned,
            self.cancelled,
            self.dropped,
        )


class Machine[D]:
    """Schedules the jobs of one response and never touches a thread, loop or clock.

    Each event takes the driver's monotonic `now` and returns the commands to run.
    """

    __slots__ = (
        "_admission",
        "_alone",
        "_capacity",
        "_caps",
        "_closed",
        "_coalesce",
        "_commands",
        "_counts",
        "_deadline",
        "_emitted",
        "_entries",
        "_facts",
        "_first",
        "_heap",
        "_live",
        "_loop",
        "_loop_open",
        "_now",
        "_placer",
        "_plan",
        "_pool",
        "_pool_open",
        "_ready",
        "_ready_since",
        "_request",
        "_request_open",
        "_started",
        "_units",
        "_unwritten",
        "_wake",
        "_writing",
    )

    def __init__(
        self,
        plan: Plan[D],
        caps: Caps,
        facts: ForegroundFacts,
        placer: Placer,
        admission: Admitter,
        *,
        deadline: float,
        coalesce: float,
    ) -> None:
        """Hold the plan, `deadline` being the monotonic instant `DEADLINE` ends."""
        self._plan = plan
        self._caps = caps
        self._facts = facts
        self._placer = placer
        self._admission = admission
        self._deadline = deadline
        self._coalesce = coalesce
        self._capacity = Capacity(0, 0, 0, 0.0)
        self._commands: list[Command[D]] = []
        self._counts = _Counts()
        self._entries: dict[str, _Entry[D]] = {}
        self._heap: list[tuple[float, tuple[int, ...], str]] = []
        self._pool: list[_Unit[D]] = []
        self._request: list[_Unit[D]] = []
        self._loop: list[_Unit[D]] = []
        self._ready: list[tuple[tuple[int, ...], Outcome[D] | Settlement[D]]] = []
        self._writing: set[str] = set()
        self._emitted: set[str] = set()
        self._ready_since = 0.0
        self._pool_open = 0
        self._request_open = 0
        self._loop_open = 0
        self._unwritten = 0
        self._live = 0
        self._now = 0.0
        self._wake: float | None = None
        self._first = True
        self._started = False
        self._closed = False
        self._units: dict[str, _Unit[D]] = {}
        self._alone = len(plan.units) == 1 and isinstance(plan.units[0], Job)
        for index, unit in enumerate(plan.units):
            if isinstance(unit, Group):
                for member, job in enumerate(unit.members):
                    self._add(job, (index, member))
            else:
                self._add(unit, (index,))
        for index, job in enumerate(plan.expired, start=len(plan.units)):
            self._add(job, (index,))

    @property
    def closed(self) -> bool:
        """Whether `Finish` was issued, after which every event is ignored."""
        return self._closed

    @property
    def totals(self) -> Totals:
        """Return the counts the signals report so far."""
        return self._counts.frozen()

    def end(self, job_id: str) -> End | None:
        """Return how the job settled, `None` while it is pending."""
        return self._entries[job_id].end

    def start(self, capacity: Capacity, now: float) -> list[Command[D]]:
        """Place every unit, settle the jobs over the cap and dispatch what may run."""
        if self._closed:
            return []
        if self._started:
            msg = "the machine was started twice"
            raise ProtocolError(msg)
        self._started = True
        self._capacity = capacity
        now = self._clock(now)
        for unit in self._plan.units:
            grouped = isinstance(unit, Group)
            members = unit.members if isinstance(unit, Group) else (unit,)
            entries = tuple(self._entries[job.id] for job in members)
            self._queue(entries, grouped=grouped, alone=self._alone)
        for job in self._plan.expired:
            self._end(
                self._entries[job.id],
                End.TIMEOUT,
                Settlement(job, ErrorKind.TIMEOUT),
                now,
            )
        return self._advance(now) or self._after(now)

    def capacity(self, capacity: Capacity, now: float) -> list[Command[D]]:
        """Take a fresh pool reading and admit again every unit still queued."""
        if self._closed:
            return []
        now = self._started_clock(now)
        self._capacity = capacity
        if done := self._advance(now):
            return done
        self._pump_pool(now, everyone=True)
        return self._after(now)

    def tick(self, now: float) -> list[Command[D]]:
        """Let time pass, settling what ran out of time and writing what waited."""
        if self._closed:
            return []
        now = self._started_clock(now)
        return self._advance(now) or self._after(now)

    def finished(
        self,
        job_id: str,
        result: Rendered[D] | BaseException,
        timing: Timing,
        now: float,
    ) -> list[Command[D]]:
        """Take the result of one task, dropped when its job already settled.

        Every task of a run command reports once, unless its unit was abandoned.
        """
        if self._closed:
            return []
        now = self._started_clock(now)
        entry = self._entries.get(job_id)
        if entry is None or entry.unit is None or not entry.commanded or entry.reported:
            msg = f"the job {job_id} was not running"
            raise ProtocolError(msg)
        if not isinstance(result, BaseException):
            self._check_nested(entry, result)
        if done := self._advance(now):
            return done
        entry.reported = True
        unit = entry.unit
        if not unit.abandoned:
            unit.open -= 1
            if not unit.open:
                self._vacate(unit)
        if entry.end is None:
            self._accept(entry, unit, result, timing, now)
        return self._after(now)

    def written(self, job_id: str, now: float) -> list[Command[D]]:
        """Release the credit of one written outcome and submit the jobs it nests."""
        if self._closed:
            return []
        now = self._started_clock(now)
        if job_id not in self._writing:
            msg = f"the outcome {job_id} was not being written"
            raise ProtocolError(msg)
        if done := self._advance(now):
            return done
        self._writing.discard(job_id)
        entry = self._entries[job_id]
        if entry.credited:
            entry.credited = False
            self._unwritten -= 1
        if isinstance(entry.item, Outcome):
            for index, job in enumerate(entry.item.rendered.nested):
                child = self._add(job, (*entry.ordinal, index))
                self._queue((child,), grouped=False, alone=False)
        return self._after(now)

    def refused(self, unit_id: str, now: float) -> list[Command[D]]:
        """Send a request-lane unit no foreground permit could start to the pool."""
        if self._closed:
            return []
        now = self._started_clock(now)
        unit = self._units.get(unit_id)
        if (
            unit is None
            or not unit.dispatched
            or unit.lane is not Lane.REQUEST
            or unit.placement.forced
            or any(entry.reported for entry in unit.entries)
        ):
            msg = f"the unit {unit_id} was not waiting for a foreground permit"
            raise ProtocolError(msg)
        for entry in unit.entries:
            entry.commanded = False
        unit.dispatched = False
        unit.open = 0
        self._request_open -= 1
        unit.placement = Placement(Verdict.FOREGROUND_FULL)
        unit.lane = Lane.POOL
        unit.keep_off_request = True
        bisect.insort(self._pool, unit, key=_ordinal)
        return self._advance(now) or self._after(now)

    def full(self, unit_id: str, now: float) -> list[Command[D]]:
        """Hand a pool unit the full queue refused to the request lane, or shed it.

        A unit the request lane already refused is shed, so none goes round twice.
        """
        if self._closed:
            return []
        now = self._started_clock(now)
        unit = self._units.get(unit_id)
        if (
            unit is None
            or not unit.dispatched
            or unit.lane is not Lane.POOL
            or any(entry.reported for entry in unit.entries)
        ):
            msg = f"the unit {unit_id} was not submitted to the pool"
            raise ProtocolError(msg)
        for entry in unit.entries:
            entry.commanded = False
        unit.dispatched = False
        unit.open = 0
        self._pool_open -= 1
        if unit.keep_off_request:
            self._shed(unit, now)
        else:
            unit.lane = Lane.REQUEST
            bisect.insort(self._request, unit, key=_ordinal)
        return self._advance(now) or self._after(now)

    def cancel(self, now: float) -> list[Command[D]]:
        """Close the response for a client that left, abandoning what still runs."""
        if self._closed:
            return []
        now = self._clock(now)
        abandoned: dict[str, None] = {}
        for entry in self._entries.values():
            if entry.end is not None:
                continue
            unit = entry.unit
            end = End.CANCELLED
            if unit is not None and unit.dispatched and not unit.abandoned:
                abandoned[unit.id] = None
                if unit.lane is not Lane.LOOP:
                    end = End.ABANDONED
            self._end(entry, end, None, now)
        self._counts.dropped += len(self._ready)
        self._ready.clear()
        if abandoned:
            self._command(Abandon(tuple(abandoned)))
        self._finish(aborted=True, cut=False)
        return self._out()

    def _clock(self, now: float) -> float:
        self._now = max(self._now, now)
        return self._now

    def _started_clock(self, now: float) -> float:
        if not self._started:
            msg = "an event arrived before start"
            raise ProtocolError(msg)
        return self._clock(now)

    def _add(self, job: Job[D], ordinal: tuple[int, ...]) -> _Entry[D]:
        if job.id in self._entries:
            msg = f"the job {job.id} was queued twice"
            raise ProtocolError(msg)
        entry = _Entry(job, ordinal, min(job.deadline, self._deadline))
        self._entries[job.id] = entry
        self._live += 1
        heapq.heappush(self._heap, (entry.deadline, ordinal, job.id))
        return entry

    def _check_nested(self, entry: _Entry[D], rendered: Rendered[D]) -> None:
        for job in rendered.nested:
            if job.parent != entry.job.id or job.depth > self._caps.depth:
                msg = f"the job {job.id} is no nested job of {entry.job.id}"
                raise ProtocolError(msg)

    def _queue(
        self, entries: tuple[_Entry[D], ...], *, grouped: bool, alone: bool
    ) -> None:
        job = entries[0].job
        placement = self._placer(job, self._facts, alone=alone)
        lane = Lane.LOOP if job.asynchronous else placement.lane
        unit = _Unit(job.id, entries, placement, lane, grouped=grouped)
        for entry in entries:
            entry.unit = unit
        self._units[unit.id] = unit
        lanes = {Lane.POOL: self._pool, Lane.REQUEST: self._request}
        bisect.insort(lanes.get(lane, self._loop), unit, key=_ordinal)

    def _advance(self, now: float) -> list[Command[D]]:
        self._expire(now)
        if now < self._deadline + self._caps.write_grace:
            return []
        self._counts.dropped += len(self._ready)
        self._ready.clear()
        self._finish(aborted=False, cut=True)
        return self._out()

    def _expire(self, now: float) -> None:
        heap = self._heap
        while heap and heap[0][0] <= now:
            entry = self._entries[heapq.heappop(heap)[2]]
            if entry.end is None:
                self._timeout(entry, now)

    def _timeout(self, entry: _Entry[D], now: float) -> None:
        unit = entry.unit
        if unit is None or not unit.dispatched:
            placement = None if unit is None else unit.placement
            timing = Timing(placement=placement)
        else:
            timing = Timing(unit.lane, unit.placement)
            if unit.lane is not Lane.LOOP:
                self._counts.late += 1
        self._end(
            entry, End.TIMEOUT, Settlement(entry.job, ErrorKind.TIMEOUT, timing), now
        )
        if (
            unit is not None
            and unit.dispatched
            and unit.lane is Lane.LOOP
            and not unit.abandoned
            and not unit.live()
        ):
            unit.abandoned = True
            self._loop_open -= 1
            self._command(Abandon((unit.id,)))

    def _accept(
        self,
        entry: _Entry[D],
        unit: _Unit[D],
        result: Rendered[D] | BaseException,
        timing: Timing,
        now: float,
    ) -> None:
        timing = Timing(
            timing.lane or unit.lane,
            timing.placement or unit.placement,
            timing.waited,
            timing.ran,
        )
        kind: ErrorKind | None
        if isinstance(result, BaseException):
            kind = (
                result.kind
                if isinstance(result, BoundaryRefusedError)
                else ErrorKind.ERROR
            )
            item: Outcome[D] | Settlement[D] = Settlement(
                entry.job, kind, timing, result
            )
        else:
            kind = result.kind
            item = Outcome(entry.job.id, result, timing)
        self._end(entry, _ENDS.get(kind, End.FAILED), item, now)

    def _end(
        self,
        entry: _Entry[D],
        end: End,
        item: Outcome[D] | Settlement[D] | None,
        now: float,
    ) -> None:
        if entry.end is not None:
            raise AssertionError(_SETTLED_TWICE, entry.job.id)
        entry.end = end
        entry.item = item
        self._live -= 1
        counter = _COUNTERS.get(end)
        if counter is not None:
            setattr(self._counts, counter, getattr(self._counts, counter) + 1)
        if item is not None:
            self._release(entry, now)

    def _release(self, entry: _Entry[D], now: float) -> None:
        unit = entry.unit
        members = unit.entries if unit is not None and unit.grouped else (entry,)
        if any(member.end is None for member in members):
            return
        if not self._ready:
            self._ready_since = now
        self._ready.extend(
            (member.ordinal, member.item)
            for member in members
            if member.item is not None
        )

    def _vacate(self, unit: _Unit[D]) -> None:
        if unit.lane is Lane.POOL:
            self._pool_open -= 1
        elif unit.lane is Lane.REQUEST:
            self._request_open -= 1
        else:
            self._loop_open -= 1

    def _dispatch(self, unit: _Unit[D]) -> Unit[D]:
        tasks: list[Task[D]] = []
        for entry in unit.live():
            if entry.commanded:
                raise AssertionError(_COMMANDED_TWICE, entry.job.id)
            entry.commanded = True
            tasks.append(Task(entry.job, entry.deadline))
        unit.dispatched = True
        unit.open = len(tasks)
        if unit.lane is Lane.POOL:
            self._pool_open += 1
        elif unit.lane is Lane.REQUEST:
            self._request_open += 1
        else:
            self._loop_open += 1
        return Unit(unit.id, tuple(tasks), unit.placement)

    def _shed(self, unit: _Unit[D], now: float) -> None:
        timing = Timing(placement=unit.placement)
        for entry in unit.live():
            item = Settlement(entry.job, ErrorKind.OVERLOADED, timing)
            self._end(entry, End.SHED, item, now)

    def _pump_pool(self, now: float, *, everyone: bool) -> None:
        queue, self._pool = self._pool, []
        for position, unit in enumerate(queue):
            if not unit.live():
                continue
            free = self._pool_open < self._caps.per_request
            if not (free or everyone):
                self._pool.extend(queue[position:])
                return
            verdict = self._admission.admit(
                self._capacity, forced=unit.placement.forced
            )
            if verdict is Admit.SHED:
                self._shed(unit, now)
            elif verdict is Admit.REQUEST and not unit.keep_off_request:
                unit.lane = Lane.REQUEST
                bisect.insort(self._request, unit, key=_ordinal)
            elif free:
                self._command(RunPool(self._dispatch(unit)))
            else:
                self._pool.append(unit)

    def _pump_request(self) -> None:
        if self._request_open:
            return
        units: list[Unit[D]] = []
        while self._request:
            unit = self._request.pop(0)
            if not unit.live():
                continue
            units.append(self._dispatch(unit))
            if self._facts.server is not Server.ASGI:
                break
        if units:
            self._command(RunRequest(tuple(units)))

    def _pump_loop(self) -> None:
        atomic = bool(self._facts.atomic)
        credit = self._caps.per_request * 2
        while self._loop and not (atomic and self._loop_open):
            live = self._loop[0].live()
            if live and self._unwritten and self._unwritten + len(live) > credit:
                return
            unit = self._loop.pop(0)
            if not live:
                continue
            for entry in live:
                entry.credited = True
            self._unwritten += len(live)
            self._command(StartLoop(self._dispatch(unit)))

    def _flush(self, now: float) -> None:
        if self._writing or not self._ready:
            return
        if not self._first and self._live and now < self._ready_since + self._coalesce:
            return
        batch = sorted(self._ready, key=_first_of)
        self._ready = []
        items = tuple(item for _, item in batch)
        for item in items:
            if item.id in self._emitted:
                raise AssertionError(_EMITTED_TWICE, item.id)
            self._emitted.add(item.id)
            self._writing.add(item.id)
        self._command(Emit(items, first=self._first))
        self._first = False

    def _schedule(self) -> None:
        heap = self._heap
        while heap and self._entries[heap[0][2]].end is not None:
            heapq.heappop(heap)
        at = self._deadline + self._caps.write_grace
        if heap:
            at = min(at, heap[0][0])
        if self._ready and not self._writing:
            at = min(at, self._ready_since + self._coalesce)
        if at != self._wake:
            self._wake = at
            self._command(Wake(at))

    def _after(self, now: float) -> list[Command[D]]:
        self._expire(now)
        self._pump_pool(now, everyone=False)
        self._pump_request()
        self._pump_loop()
        self._flush(now)
        if self._live or self._ready or self._writing:
            self._schedule()
        else:
            self._finish(aborted=False, cut=False)
        return self._out()

    def _finish(self, *, aborted: bool, cut: bool) -> None:
        self._command(Finish(aborted, cut, self._counts.frozen()))
        self._closed = True

    def _command(self, command: Command[D]) -> None:
        if self._closed:
            raise AssertionError(_AFTER_FINISH, command)
        self._commands.append(command)

    def _out(self) -> list[Command[D]]:
        commands, self._commands = self._commands, []
        return commands


def _first_of[T](pair: tuple[tuple[int, ...], T]) -> tuple[int, ...]:
    return pair[0]


_ENDS: Final = {
    None: End.RAN,
    ErrorKind.TIMEOUT: End.TIMEOUT,
    ErrorKind.OVERLOADED: End.SHED,
}

_COUNTERS: Final = {
    End.TIMEOUT: "timeouts",
    End.SHED: "shed",
    End.ABANDONED: "abandoned",
    End.CANCELLED: "cancelled",
}


__all__ = [
    "Abandon",
    "Admitter",
    "Command",
    "Emit",
    "End",
    "Finish",
    "Machine",
    "Placer",
    "ProtocolError",
    "RunPool",
    "RunRequest",
    "Settlement",
    "StartLoop",
    "Task",
    "Totals",
    "Unit",
    "Wake",
]
