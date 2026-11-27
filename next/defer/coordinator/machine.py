"""The state machine of one response, fed timed events and answering with commands."""

from __future__ import annotations

import bisect
import enum
import heapq
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from next.boundaries.failures import ErrorKind
from next.defer.errors import BoundaryRefusedError, InvariantError, ProtocolError
from next.defer.markers import (
    Admit,
    Capacity,
    Group,
    Job,
    Lane,
    LaneChoice,
    Outcome,
    PoolVerdict,
    ServerKind,
    Timing,
)


if TYPE_CHECKING:
    from next.defer.admission import Admission
    from next.defer.markers import Caps, JobPlan, LaneFacts, Rendered


_SETTLED_TWICE: Final = "a job reached a second terminal state"
_COMMANDED_TWICE: Final = "a job was commanded twice"
_EMITTED_TWICE: Final = "an outcome was emitted twice"
_AFTER_FINISH: Final = "a command followed Finish"


class End(enum.StrEnum):
    """The one terminal state every job of a response reaches."""

    RAN = "ran"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SHED = "shed"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"


@dataclass(frozen=True, slots=True)
class Task:
    """One job a lane runs, and the instant past which it no longer starts."""

    job: Job
    deadline: float


@dataclass(frozen=True, slots=True)
class Unit:
    """Jobs a lane runs one after another, a lone job or the members of a group."""

    id: str
    tasks: tuple[Task, ...]
    placement: LaneChoice


@dataclass(frozen=True, slots=True)
class RunPool:
    """Submit one unit to the pool lane."""

    unit: Unit


@dataclass(frozen=True, slots=True)
class RunRequest:
    """Run units on the request thread in order, as one hop under ASGI."""

    units: tuple[Unit, ...]


@dataclass(frozen=True, slots=True)
class Abandon:
    """Stop waiting for running units, dropping their pool work no thread claimed."""

    ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Emit:
    """Write these outcomes in document order as one write with one flush."""

    items: tuple[Outcome, ...]
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


type Command = RunPool | RunRequest | Abandon | Emit | Wake | Finish


class _Phase(enum.Enum):
    QUEUED = enum.auto()
    RUNNING = enum.auto()


@dataclass(slots=True, eq=False)
class _Entry:
    job: Job
    ordinal: tuple[int, ...]
    deadline: float
    unit: _Unit | None = None
    end: End | None = None
    item: Outcome | None = None
    commanded: bool = False
    reported: bool = False


@dataclass(slots=True, eq=False)
class _Unit:
    id: str
    entries: tuple[_Entry, ...]
    placement: LaneChoice
    lane: Lane
    grouped: bool = False
    phase: _Phase = _Phase.QUEUED
    keep_off_request: bool = False
    open: int = 0

    @property
    def ordinal(self) -> tuple[int, ...]:
        return self.entries[0].ordinal

    def pending(self) -> bool:
        return any(entry.end is None for entry in self.entries)

    def live(self) -> list[_Entry]:
        return [entry for entry in self.entries if entry.end is None]


def _ordinal(unit: _Unit) -> tuple[int, ...]:
    return unit.ordinal


class Machine:
    """Schedules the jobs of one response and never touches a thread, loop or clock.

    Each event takes the driver's monotonic `now` and returns the commands to run.
    """

    __slots__ = (
        "_admission",
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
        "_now",
        "_open",
        "_plan",
        "_pool",
        "_ready",
        "_ready_since",
        "_request",
        "_started",
        "_units",
        "_wake",
        "_writing",
    )

    def __init__(
        self,
        plan: JobPlan,
        caps: Caps,
        facts: LaneFacts,
        admission: Admission,
        *,
        deadline: float,
        coalesce: float,
    ) -> None:
        """Hold the plan, `deadline` being the monotonic instant `DEADLINE` ends."""
        self._plan = plan
        self._caps = caps
        self._facts = facts
        self._admission = admission
        self._deadline = deadline
        self._coalesce = coalesce
        self._capacity = Capacity(0, 0, 0, 0.0)
        self._commands: list[Command] = []
        self._counts: Counter[str] = Counter()
        self._entries: dict[str, _Entry] = {}
        self._heap: list[tuple[float, tuple[int, ...], str]] = []
        self._pool: list[_Unit] = []
        self._request: list[_Unit] = []
        self._ready: list[tuple[tuple[int, ...], Outcome]] = []
        self._writing: tuple[Outcome, ...] = ()
        self._emitted: set[str] = set()
        self._ready_since = 0.0
        self._open = dict.fromkeys(Lane, 0)
        self._live = 0
        self._now = 0.0
        self._wake: float | None = None
        self._first = True
        self._started = False
        self._closed = False
        self._units: dict[str, _Unit] = {}
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
        return Totals(**self._counts)

    def start(self, capacity: Capacity, now: float) -> list[Command]:
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
            self._queue(entries, grouped=grouped)
        for job in self._plan.expired:
            self._end(
                self._entries[job.id],
                End.TIMEOUT,
                Outcome(job, settled=ErrorKind.TIMEOUT),
                now,
            )
        return self._advance(now) or self._after(now)

    def capacity(self, capacity: Capacity, now: float) -> list[Command]:
        """Take a fresh pool reading and admit the units past the credit again."""
        if self._closed:
            return []
        now = self._started_clock(now)
        self._capacity = capacity
        if done := self._advance(now):
            return done
        self._pump_pool(now, everyone=True)
        return self._after(now)

    def tick(self, now: float) -> list[Command]:
        """Let time pass, settling what ran out of time and writing what waited."""
        if self._closed:
            return []
        now = self._started_clock(now)
        return self._advance(now) or self._after(now)

    def finished(
        self, job_id: str, result: Rendered | BaseException, timing: Timing, now: float
    ) -> list[Command]:
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
        unit.open -= 1
        if not unit.open:
            self._open[unit.lane] -= 1
        if entry.end is None:
            self._accept(entry, unit, result, timing, now)
        return self._after(now)

    def written(self, now: float) -> list[Command]:
        """Take the batch being written as written and queue the jobs it nests."""
        if self._closed:
            return []
        now = self._started_clock(now)
        if not self._writing:
            msg = "no batch was being written"
            raise ProtocolError(msg)
        if done := self._advance(now):
            return done
        batch, self._writing = self._writing, ()
        for item in batch:
            entry = self._entries[item.id]
            if item.rendered is not None:
                for index, job in enumerate(item.rendered.nested):
                    child = self._add(job, (*entry.ordinal, index))
                    self._queue((child,), grouped=False)
        return self._after(now)

    def refused(self, unit_id: str, now: float) -> list[Command]:
        """Send a request-lane unit no request permit could start to the pool."""
        if self._closed:
            return []
        now = self._started_clock(now)
        unit = self._units.get(unit_id)
        if (
            unit is None
            or unit.phase is not _Phase.RUNNING
            or unit.lane is not Lane.REQUEST
            or unit.placement.forced
            or any(entry.reported for entry in unit.entries)
        ):
            msg = f"the unit {unit_id} was not waiting for a request permit"
            raise ProtocolError(msg)
        self._requeue(unit)
        unit.placement = LaneChoice(PoolVerdict.REQUEST_FULL)
        unit.lane = Lane.POOL
        unit.keep_off_request = True
        bisect.insort(self._pool, unit, key=_ordinal)
        return self._advance(now) or self._after(now)

    def full(self, unit_id: str, now: float) -> list[Command]:
        """Hand a pool unit the full queue refused to the request lane, or shed it.

        A unit the request lane already refused is shed, so none goes round twice.
        """
        if self._closed:
            return []
        now = self._started_clock(now)
        unit = self._units.get(unit_id)
        if (
            unit is None
            or unit.phase is not _Phase.RUNNING
            or unit.lane is not Lane.POOL
            or any(entry.reported for entry in unit.entries)
        ):
            msg = f"the unit {unit_id} was not submitted to the pool"
            raise ProtocolError(msg)
        self._requeue(unit)
        if unit.keep_off_request:
            self._shed(unit, now)
        else:
            unit.lane = Lane.REQUEST
            bisect.insort(self._request, unit, key=_ordinal)
        return self._advance(now) or self._after(now)

    def cancel(self, now: float) -> list[Command]:
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
            if unit is not None and unit.phase is _Phase.RUNNING:
                abandoned[unit.id] = None
                end = End.ABANDONED
            self._end(entry, end, None, now)
        self._counts["dropped"] += len(self._ready)
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

    def _add(self, job: Job, ordinal: tuple[int, ...]) -> _Entry:
        if job.id in self._entries:
            msg = f"the job {job.id} was queued twice"
            raise ProtocolError(msg)
        entry = _Entry(job, ordinal, min(job.deadline, self._deadline))
        self._entries[job.id] = entry
        self._live += 1
        heapq.heappush(self._heap, (entry.deadline, ordinal, job.id))
        return entry

    def _check_nested(self, entry: _Entry, rendered: Rendered) -> None:
        for job in rendered.nested:
            if job.parent != entry.job.id or job.depth > self._caps.depth:
                msg = f"the job {job.id} is no nested job of {entry.job.id}"
                raise ProtocolError(msg)

    def _queue(self, entries: tuple[_Entry, ...], *, grouped: bool) -> None:
        job = entries[0].job
        rule = self._facts.rule(job.options)
        placement = LaneChoice(PoolVerdict.POOL if rule is None else rule)
        lane = placement.lane
        unit = _Unit(job.id, entries, placement, lane, grouped=grouped)
        for entry in entries:
            entry.unit = unit
        self._units[unit.id] = unit
        queue = self._pool if lane is Lane.POOL else self._request
        bisect.insort(queue, unit, key=_ordinal)

    def _advance(self, now: float) -> list[Command]:
        self._expire(now)
        if now < self._deadline + self._caps.write_grace:
            return []
        self._counts["dropped"] += len(self._ready)
        self._ready.clear()
        self._finish(aborted=False, cut=True)
        return self._out()

    def _expire(self, now: float) -> None:
        heap = self._heap
        while heap and heap[0][0] <= now:
            entry = self._entries[heapq.heappop(heap)[2]]
            if entry.end is None:
                self._timeout(entry, now)

    def _timeout(self, entry: _Entry, now: float) -> None:
        unit = entry.unit
        if unit is None or unit.phase is _Phase.QUEUED:
            placement = None if unit is None else unit.placement
            timing = Timing(placement=placement)
        else:
            timing = Timing(unit.lane, unit.placement)
            self._counts["late"] += 1
        self._end(
            entry,
            End.TIMEOUT,
            Outcome(entry.job, timing=timing, settled=ErrorKind.TIMEOUT),
            now,
        )

    def _accept(
        self,
        entry: _Entry,
        unit: _Unit,
        result: Rendered | BaseException,
        timing: Timing,
        now: float,
    ) -> None:
        timing = Timing(
            timing.lane or unit.lane,
            timing.placement or unit.placement,
            timing.waited,
            timing.ran,
        )
        if isinstance(result, BaseException):
            settled = (
                result.kind
                if isinstance(result, BoundaryRefusedError)
                else ErrorKind.ERROR
            )
            item = Outcome(entry.job, timing=timing, settled=settled, error=result)
        else:
            item = Outcome(entry.job, result, timing)
        self._end(entry, _ENDS.get(item.kind, End.FAILED), item, now)

    def _end(self, entry: _Entry, end: End, item: Outcome | None, now: float) -> None:
        if entry.end is not None:
            raise InvariantError(_SETTLED_TWICE, entry.job.id)
        entry.end = end
        entry.item = item
        self._live -= 1
        counter = _COUNTERS.get(end)
        if counter is not None:
            self._counts[counter] += 1
        if item is not None:
            self._release(entry, now)

    def _release(self, entry: _Entry, now: float) -> None:
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

    def _requeue(self, unit: _Unit) -> None:
        for entry in unit.entries:
            entry.commanded = False
        unit.phase = _Phase.QUEUED
        unit.open = 0
        self._open[unit.lane] -= 1

    def _dispatch(self, unit: _Unit) -> Unit:
        tasks: list[Task] = []
        for entry in unit.live():
            if entry.commanded:
                raise InvariantError(_COMMANDED_TWICE, entry.job.id)
            entry.commanded = True
            tasks.append(Task(entry.job, entry.deadline))
        unit.phase = _Phase.RUNNING
        unit.open = len(tasks)
        self._open[unit.lane] += 1
        return Unit(unit.id, tuple(tasks), unit.placement)

    def _shed(self, unit: _Unit, now: float) -> None:
        timing = Timing(placement=unit.placement)
        for entry in unit.live():
            item = Outcome(entry.job, timing=timing, settled=ErrorKind.OVERLOADED)
            self._end(entry, End.SHED, item, now)

    def _pump_pool(self, now: float, *, everyone: bool) -> None:
        """Admit the queued units the credit takes, `everyone` admitting past it.

        Past the credit one verdict serves the queue, so the first unit left holds all.
        """
        queue, self._pool = self._pool, []
        for position, unit in enumerate(queue):
            if not unit.pending():
                continue
            free = self._open[Lane.POOL] < self._caps.per_request
            if not (free or everyone):
                self._pool.extend(queue[position:])
                return
            verdict = self._admission.admit(self._capacity)
            if verdict is Admit.SHED:
                self._shed(unit, now)
            elif verdict is Admit.REQUEST and not unit.keep_off_request:
                unit.lane = Lane.REQUEST
                bisect.insort(self._request, unit, key=_ordinal)
            elif free:
                self._command(RunPool(self._dispatch(unit)))
            elif verdict is Admit.POOL:
                self._pool.extend(queue[position:])
                return
            else:
                self._pool.append(unit)

    def _pump_request(self) -> None:
        if self._open[Lane.REQUEST]:
            return
        units: list[Unit] = []
        while self._request:
            unit = self._request.pop(0)
            if not unit.pending():
                continue
            units.append(self._dispatch(unit))
            if self._facts.server is not ServerKind.ASGI:
                break
        if units:
            self._command(RunRequest(tuple(units)))

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
                raise InvariantError(_EMITTED_TWICE, item.id)
            self._emitted.add(item.id)
        self._writing = items
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

    def _after(self, now: float) -> list[Command]:
        self._expire(now)
        self._pump_pool(now, everyone=False)
        self._pump_request()
        self._flush(now)
        if self._live or self._ready or self._writing:
            self._schedule()
        else:
            self._finish(aborted=False, cut=False)
        return self._out()

    def _finish(self, *, aborted: bool, cut: bool) -> None:
        self._command(Finish(aborted, cut, Totals(**self._counts)))
        self._closed = True

    def _command(self, command: Command) -> None:
        if self._closed:
            raise InvariantError(_AFTER_FINISH, command)
        self._commands.append(command)

    def _out(self) -> list[Command]:
        commands, self._commands = self._commands, []
        return commands


def _first_of[T](pair: tuple[tuple[int, ...], T]) -> tuple[int, ...]:
    return pair[0]


_ENDS: Final = {
    None: End.RAN,
    ErrorKind.TIMEOUT: End.TIMEOUT,
    ErrorKind.OVERLOADED: End.SHED,
}

_COUNTERS: Final[dict[End, str]] = {
    End.TIMEOUT: "timeouts",
    End.SHED: "shed",
    End.ABANDONED: "abandoned",
    End.CANCELLED: "cancelled",
}


__all__ = [
    "Abandon",
    "Command",
    "Emit",
    "End",
    "Finish",
    "Machine",
    "RunPool",
    "RunRequest",
    "Task",
    "Totals",
    "Unit",
    "Wake",
]
