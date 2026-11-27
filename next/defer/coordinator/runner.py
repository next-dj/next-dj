"""Run one task of a response in a fresh copy of its snapshot, timed and reported."""

import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, replace
from typing import Final

from next.bridge.context import ContextSnapshot
from next.bridge.deadline import deadline
from next.defer.connections import Replayable, deadline_guard, job_guard
from next.defer.errors import BoundaryDeadlineError
from next.defer.executors import BoundaryExecutor
from next.defer.markers import Job, Lane, LaneChoice, LaneFacts, Rendered, Timing
from next.defer.signals import boundary_job_finished, boundary_job_started
from next.deps.loader import CountedJob
from next.deps.scope import RequestScope
from next.diagnostics import Capture

from .machine import Task


_NS: Final = 1_000_000_000
_MS: Final = 1_000_000


@dataclass(frozen=True, slots=True)
class Runtime:
    """What every job of one response runs with, read from its session once."""

    snapshot: ContextSnapshot
    states: Replayable
    facts: LaneFacts
    executor: BoundaryExecutor
    scope: RequestScope | None = None


@dataclass(frozen=True, slots=True)
class Report:
    """How one task of a unit ended, with what its job returned or raised."""

    unit: str
    job: str
    result: Rendered | BaseException
    timing: Timing


def skipped(unit: str, task: Task, placement: LaneChoice) -> Report:
    """Return the report of a task that never started, as its deadline had passed."""
    return Report(
        unit, task.job.id, BoundaryDeadlineError(), Timing(placement=placement)
    )


def run(
    runtime: Runtime,
    unit: str,
    task: Task,
    placement: LaneChoice,
    *,
    lane: Lane,
    queued: float,
    count: CountedJob | None,
) -> Report:
    """Run the sync body of `task` on this thread, or skip it past its deadline.

    The deadline reaches PostgreSQL on both lanes, only a pool thread replays states.
    """
    started = time.monotonic()
    if started >= task.deadline:
        return skipped(unit, task, placement)
    timing = Timing(lane, placement, started - queued)
    snapshot = runtime.snapshot
    context = snapshot.get()
    if context is None:
        try:
            with deadline(task.deadline):
                context = snapshot.restore()
        except TimeoutError:
            return skipped(unit, task, placement)
    return context.copy().run(_run, runtime, unit, task, timing, count)


def _counting(count: CountedJob | None) -> AbstractContextManager[None]:
    """Return what marks the body as the counted job of its unit, if it has one."""
    return nullcontext() if count is None else count.running()


def _run(
    runtime: Runtime, unit: str, task: Task, timing: Timing, count: CountedJob | None
) -> Report:
    """Run the body, an exception settling the job, an interrupt leaving the thread."""
    job = task.job
    guard = (
        job_guard(runtime.states, deadline=task.deadline)
        if timing.lane is Lane.POOL
        else deadline_guard(deadline=task.deadline)
    )
    capture = Capture()
    wall = time.perf_counter_ns()
    with capture, deadline(task.deadline), guard, _counting(count):
        rendered = _observed(job, timing, job.run)
    ran = (time.perf_counter_ns() - wall) / _NS
    result = rendered if capture.error is None else capture.error
    return Report(unit, job.id, result, replace(timing, ran=ran))


def _observed(job: Job, timing: Timing, body: Callable[[], Rendered]) -> Rendered:
    """Run `body` between the two job signals, sent on its thread and in its context.

    A receiver of the first sees the deadline and the replayed connection state.
    """
    started = boundary_job_started.has_listeners(Job)
    finished = boundary_job_finished.has_listeners(Job)
    if not (started or finished):
        return body()
    site = job.site
    placement = timing.placement
    facts: dict[str, object] = {
        "id": job.id,
        "name": site.name,
        "addressing": site.addressing,
        "mode": site.mode,
        "lane": timing.lane,
        "reason": None if placement is None else placement.reason,
    }
    if started:
        boundary_job_started.send_robust(sender=Job, **facts)
    error: BaseException | None = None
    wall = time.perf_counter_ns()
    try:
        return body()
    except BaseException as raised:
        error = raised
        raise
    finally:
        if finished:
            run_ms = (time.perf_counter_ns() - wall) / _MS
            boundary_job_finished.send_robust(
                sender=Job, **facts, error=error, run_ms=run_ms
            )


__all__ = ["Report", "Runtime", "run", "skipped"]
