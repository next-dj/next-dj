"""Run one task of a response in a fresh copy of its snapshot, timed and reported."""

import asyncio
import logging
import time
from contextlib import AbstractContextManager, nullcontext
from contextvars import Context
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final, cast

from asgiref.sync import AsyncToSync

from next.defer import deadline
from next.deps.cache import RequestMemo, request_memo
from next.deps.loader import counted_job
from next.deps.runner import run_on_request_thread, segment_runner
from next.deps.scope import RequestScope
from next.suspense.connections import Replayable, job_guard
from next.suspense.coordinator.machine import Task
from next.suspense.coordinator.placement import CostTable
from next.suspense.errors import BoundaryDeadlineError
from next.suspense.executors import BoundaryExecutor, RequestThreadExecutor, served_cpu
from next.suspense.markers import ForegroundFacts, Lane, Placement, Rendered, Timing


if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine


logger = logging.getLogger("next.suspense")

_NS: Final = 1_000_000_000
_INTERRUPTED: Final = "A deferred job raised %s, kept as the failure of its boundary."


@dataclass(frozen=True, slots=True)
class Runtime:
    """What every job of one response runs with, read from its session once.

    `hops` is the executor thread-sensitive calls of an async job reach the request
    thread through, `None` where the server's own request context routes them.
    """

    snapshot: Context
    states: Replayable
    facts: ForegroundFacts
    executor: BoundaryExecutor
    costs: CostTable
    scope: RequestScope | None = None
    hops: RequestThreadExecutor | None = None


@dataclass(frozen=True, slots=True)
class Report[D]:
    """How one task of a unit ended, with what its job returned or raised."""

    unit: str
    job: str
    result: Rendered[D] | BaseException
    timing: Timing


def skipped[D](unit: str, task: Task[D], placement: Placement) -> Report[D]:
    """Return the report of a task that never started, as its deadline had passed."""
    return Report(
        unit, task.job.id, BoundaryDeadlineError(), Timing(placement=placement)
    )


def run[D](
    runtime: Runtime,
    unit: str,
    task: Task[D],
    placement: Placement,
    *,
    lane: Lane,
    queued: float,
) -> Report[D]:
    """Run the sync body of `task` on this thread, or skip it past its deadline.

    Only a pool thread replays the request thread's thread and connection states.
    """
    started = time.monotonic()
    if started >= task.deadline:
        return skipped(unit, task, placement)
    timing = Timing(lane, placement, started - queued)
    return runtime.snapshot.copy().run(_run, runtime, unit, task, timing)


def _run[D](runtime: Runtime, unit: str, task: Task[D], timing: Timing) -> Report[D]:
    """Run the body, charging its site its own CPU and not that of hops it served.

    An exception settles the job as a logged fallback, an interrupt leaves the thread.
    """
    job = task.job
    body = cast("Callable[[], Rendered[D]]", job.run)
    guard: AbstractContextManager[None] = nullcontext()
    if timing.lane is Lane.POOL:
        guard = job_guard(runtime.states, deadline=task.deadline)
    result: Rendered[D] | Exception
    wall = time.perf_counter_ns()
    served = served_cpu()
    cpu = time.thread_time_ns()
    try:
        with deadline(task.deadline), guard, counted_job(runtime.scope, task.deadline):
            result = body()
    except Exception as error:  # noqa: BLE001
        result = error
    wall = time.perf_counter_ns() - wall
    cpu = max(0, time.thread_time_ns() - cpu - (served_cpu() - served))
    runtime.costs.observe(job.site.cost_key, wall, cpu)
    return Report(unit, job.id, result, replace(timing, ran=wall / _NS))


async def arun[D](
    runtime: Runtime, unit: str, task: Task[D], placement: Placement, *, queued: float
) -> Report[D]:
    """Run the coroutine of `task` as a task of its own in a fresh copy of the snapshot.

    Cancelling the caller cancels that task, and the cancellation propagates unreported.
    """
    started = time.monotonic()
    if started >= task.deadline:
        return skipped(unit, task, placement)
    timing = Timing(Lane.LOOP, placement, started - queued)
    loop = asyncio.get_running_loop()
    return await loop.create_task(
        _arun(runtime, unit, task, timing), context=runtime.snapshot.copy()
    )


async def _arun[D](
    runtime: Runtime, unit: str, task: Task[D], timing: Timing
) -> Report[D]:
    _enter(runtime)
    job = task.job
    body = cast("Callable[[], Coroutine[Any, Any, Rendered[D]]]", job.arun)
    result: Rendered[D] | BaseException
    wall = time.perf_counter_ns()
    try:
        with deadline(task.deadline), counted_job(runtime.scope, task.deadline):
            result = await body()
    except BaseException as error:
        if isinstance(error, asyncio.CancelledError) and _cancelling():
            raise
        if not isinstance(error, Exception):
            logger.exception(_INTERRUPTED, type(error).__name__)
        result = error
    ran = (time.perf_counter_ns() - wall) / _NS
    return Report(unit, job.id, result, replace(timing, ran=ran))


def _enter(runtime: Runtime) -> None:
    """Route the hops of this job and resolve it in a layer of the request scope.

    The scope keys a value by name alone and jobs differ in props, so writes stay local.
    """
    AsyncToSync.executors.current = runtime.hops
    segment_runner.set(run_on_request_thread)
    scope = runtime.scope
    if scope is None:
        return
    memo = request_memo.get()
    if memo is None or memo.scope is not scope:
        request_memo.set(RequestMemo(scope.layer()))
    else:
        request_memo.set(replace(memo, scope=scope.layer()))


def _cancelling() -> bool:
    """Whether the running task is being cancelled, rather than failing by itself."""
    current = asyncio.current_task()
    return current is not None and current.cancelling() > 0


__all__ = ["Report", "Runtime", "arun", "run", "skipped"]
