"""The run of a page context along its graph, serial or overlapping async steps."""

from __future__ import annotations

import asyncio
import contextvars
import time
from dataclasses import dataclass, replace
from enum import Enum, auto
from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from next.deps.cache import request_memo
from next.deps.context import ResolutionContext
from next.deps.resolver import current_resolver, sharing
from next.deps.runner import run_isolated

from .blame import blame, blame_watch
from .merge import Merge, Ordered, RunOrder, keyless_shape_error
from .variables import (
    LocalAccessor,
    Values,
    Writes,
    accessors,
    read_values,
    restore,
    scope_writes,
    settle,
)


if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from django.http import HttpRequest

    from next.deps.resolver import DependencyResolver
    from next.pages.context import ContextResult

    from .compile import ContextGraph


class _Pending(Enum):
    """A result slot of a parallel run whose step has not run or never runs."""

    PENDING = auto()
    SKIPPED = auto()


_NO_PRIOR: Final[Mapping[int, object]] = MappingProxyType({})


def _retrieved(task: asyncio.Task[object]) -> None:
    """Read the error of a finished task, so one the run abandons is never logged."""
    if not task.cancelled():
        task.exception()


async def _stop(tasks: list[asyncio.Task[object]]) -> None:
    """Cancel the tasks still running and wait until each of them has ended."""
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending)


def _failure(task: asyncio.Task[object]) -> BaseException | None:
    """Return the error `task` ended with, None for a result or a cancel."""
    return None if task.cancelled() else task.exception()


class _Run:
    """One collection of a page context along its steps, sync or async."""

    __slots__ = (
        "base",
        "error",
        "file_path",
        "filtered",
        "groups",
        "limit",
        "order",
        "prior",
        "resolver",
        "selected",
        "steps",
        "taken",
        "timings",
        "watch",
        "zones",
    )

    def __init__(
        self,
        order: Ordered,
        file_path: Path,
        base: ResolutionContext,
        zones: frozenset[str] | None = None,
        groups: int | None = None,
    ) -> None:
        """Hold what every step resolves against, a `groups` count cutting the run."""
        self.order = order
        self.steps = steps = order.steps
        self.file_path = file_path
        self.base = base
        self.zones = zones
        self.groups = groups
        self.filtered = zones is not None or groups is not None
        self.selected: frozenset[int] | None = None
        self.prior: Mapping[int, object] = _NO_PRIOR
        self.timings: dict[int, float] | None = None
        self.taken: dict[int, object] | None = None
        self.watch = blame_watch.get()
        self.resolver: DependencyResolver = current_resolver()
        self.limit = len(steps)
        self.error: BaseException | None = None

    def select(self, phase: Phase) -> dict[int, object]:
        """Run only the steps of `phase` and return the mapping its results go to."""
        self.selected = phase.selected
        self.prior = phase.prior
        self.timings = phase.timings
        self.filtered = True
        taken: dict[int, object] = {}
        self.taken = taken
        return taken

    def skipped(self, index: int) -> bool:
        """Return whether a zone GET, a phase or a group cut skips it."""
        step = self.steps[index]
        groups = self.groups
        if groups is not None and not 0 <= step.group < groups:
            return True
        selected = self.selected
        if selected is not None and index not in selected:
            return True
        zones = step.zones
        requested = self.zones
        return (
            requested is not None and zones is not None and zones.isdisjoint(requested)
        )

    def call(self, index: int, context: ResolutionContext) -> object:
        """Call one sync step against `context`, timed when the run keeps timings."""
        func = self.steps[index].func
        timings = self.timings
        if timings is None:
            return func(**self.resolver.resolve(func, context))
        started = time.perf_counter()
        value = func(**self.resolver.resolve(func, context))
        timings[index] = time.perf_counter() - started
        return value

    async def acall(self, index: int, view: Mapping[str, object]) -> object:
        """Await one async step against `view`, timed when the run keeps timings."""
        func = self.steps[index].func
        base = self.base
        cache = base.cache
        context = ResolutionContext(base.request, None, base.url_kwargs, view, cache)
        started = time.perf_counter()
        value = await self.resolver.acall(func, context)
        if self.timings is not None:
            self.timings[index] = time.perf_counter() - started
        return value

    def settle(self, index: int, value: object) -> None:
        """Keep a result for the phase, an inherited one for the blame that watches."""
        taken = self.taken
        if taken is not None:
            taken[index] = value
        watch = self.watch
        step = self.steps[index]
        if watch is not None and step.group >= 0:
            watch.settle(step.group, step.key, value)

    def run(
        self, merge: Merge, indices: Iterable[int], context: ResolutionContext
    ) -> None:
        """Run the sync steps at `indices` in order against the live `merge`.

        Steps in a row reading one mapping share a resolution context, `context` first.
        """
        steps = self.steps
        prior = self.prior
        filtered = self.filtered
        resolver = self.resolver
        timings = self.timings
        keeps = self.taken is not None or self.watch is not None
        for index in indices:
            if index in prior:
                merge.add(index, prior[index])
                continue
            if filtered and self.skipped(index):
                continue
            view = merge.reads(index)
            if view is None:
                continue
            if view is not context.context_data:
                context = ResolutionContext(
                    context.request, None, context.url_kwargs, view, context.cache
                )
            func = steps[index].func
            try:
                if timings is None:
                    value = func(**resolver.resolve(func, context))
                else:
                    value = self.call(index, context)
                merge.add(index, value)
            except Exception:
                blame(steps[index].group)
                raise
            if keeps:
                self.settle(index, value)

    async def serial(self) -> ContextResult:
        """Run every step in order, a run of sync steps as one blocking segment."""
        merge = Merge(self.order, self.file_path)
        batch: list[int] = []
        prior = self.prior
        for index, step in enumerate(self.steps):
            if index in prior:
                if batch:
                    await self._segment(tuple(batch), merge)
                    batch = []
                merge.add(index, prior[index])
                continue
            if self.skipped(index):
                continue
            if not step.is_async:
                batch.append(index)
                continue
            if batch:
                await self._segment(tuple(batch), merge)
                batch = []
            view = merge.reads(index)
            if view is None:
                continue
            try:
                value = await self.acall(index, view)
                merge.add(index, value)
            except Exception:
                blame(step.group)
                raise
            self.settle(index, value)
        if batch:
            await self._segment(tuple(batch), merge)
        return merge.result()

    async def _segment(self, indices: tuple[int, ...], merge: Merge) -> None:
        """Run consecutive sync steps against the live merge in one blocking segment."""
        await run_isolated(partial(self._batch, indices, merge), self.base)

    def _batch(
        self, indices: tuple[int, ...], merge: Merge, context: ResolutionContext
    ) -> None:
        self.run(merge, indices, context)

    async def parallel(self, graph: ContextGraph) -> ContextResult:
        """Run the layers of `graph` in order, overlapping the async steps of each.

        The failure a sequential run meets first is raised once no step before it runs.
        A dependency two steps name is shared, so the overlap computes it once.
        """
        memo = request_memo.get()
        shared = graph.shared
        token = (
            None
            if memo is None or shared <= memo.shared
            else request_memo.set(replace(memo, shared=memo.shared | shared))
        )
        results: list[object] = [_Pending.PENDING] * len(self.steps)
        prior = self.prior
        for index, value in prior.items():
            results[index] = value
        try:
            for layer in graph.layers:
                await self._layer(layer, results)
        finally:
            if token is not None:
                request_memo.reset(token)
        for index, value in enumerate(results[: self.limit]):
            if not isinstance(value, _Pending) and index not in prior:
                self.settle(index, value)
        if self.error is not None:
            raise self.error
        merge = Merge(self.order, self.file_path)
        for index, value in enumerate(results):
            if value is not _Pending.SKIPPED:
                merge.add(index, value)
        return merge.result()

    def _fail(self, index: int, error: BaseException) -> None:
        """Keep the failure earliest in run order, the one a sequential run raises."""
        if index < self.limit:
            self.limit = index
            self.error = error
            blame(self.steps[index].group)

    async def _layer(self, layer: tuple[int, ...], results: list[object]) -> None:
        """Run the steps of one layer that come before the earliest failure.

        One merge walks the finished results up to each step, which reads a copy.
        """
        calls: list[tuple[int, dict[str, object]]] = []
        fold = Merge(self.order, self.file_path)
        folded = 0
        for index in layer:
            if index >= self.limit:
                break
            if results[index] is not _Pending.PENDING:
                continue
            if self.skipped(index):
                results[index] = _Pending.SKIPPED
                continue
            for earlier in range(folded, index):
                value = results[earlier]
                if not isinstance(value, _Pending):
                    fold.add(earlier, value)
            folded = index
            view = fold.reads(index)
            if view is None:
                results[index] = _Pending.SKIPPED
                continue
            calls.append((index, dict(view)))
        if calls:
            await self._overlap(calls, results)

    def _shaped(self, index: int, value: object) -> object:
        """Return `value`, raising the shape error for a keyless step's non-mapping."""
        step = self.steps[index]
        if step.key is None and not isinstance(value, dict):
            raise keyless_shape_error(step.func, self.file_path)
        return value

    async def _step(self, index: int, view: dict[str, object]) -> object:
        """Await one async step, failing at once on a keyless step's wrong shape."""
        return self._shaped(index, await self.acall(index, view))

    async def _seeded_lane(self, lane: _Lane) -> None:
        """Resolve the shared keys of the sync steps on the loop, then run the steps.

        Keys failing for one step still let the steps before it run, as in a serial run.
        """
        base = self.base
        seeded = 0
        try:
            for index, view in lane.steps:
                context = ResolutionContext(
                    base.request, None, base.url_kwargs, view, base.cache
                )
                await self.resolver.aseed(self.steps[index].func, context)
                seeded += 1
        except Exception:
            if seeded:
                await run_isolated(partial(self._lane, lane, lane.steps[:seeded]), base)
            raise
        await run_isolated(partial(self._lane, lane, lane.steps), base)

    def _lane(
        self,
        lane: _Lane,
        steps: tuple[tuple[int, dict[str, object]], ...],
        context: ResolutionContext,
    ) -> None:
        """Run the sync steps of a layer in order, each in a context copy of its own.

        Writes go to the segment context, and it stops past `lane.stop` on a failure.
        """
        start = contextvars.copy_context()
        for index, view in steps:
            if index > lane.stop:
                return
            scope = start.copy()
            resolution = ResolutionContext(
                context.request, None, context.url_kwargs, view, context.cache
            )
            value = scope.run(self.call, index, resolution)
            shaped = self._shaped(index, value)
            written = scope_writes(scope, start)
            restore(written)
            end = scope.run(read_values, lane.tracked) if written else None
            lane.done.append((shaped, frozenset(written), end))

    async def _overlap(
        self, calls: list[tuple[int, dict[str, object]]], results: list[object]
    ) -> None:
        """Run the async steps as tasks beside one segment of the sync steps.

        A failure cancels the siblings after it and lets the ones before it finish.
        """
        start = contextvars.copy_context()
        tracked = accessors()
        steps = self.steps
        owned: list[tuple[int, asyncio.Task[object], contextvars.Context]] = []
        for index, view in calls:
            if steps[index].is_async:
                scope = start.copy()
                task = asyncio.create_task(self._step(index, view), context=scope)
                task.add_done_callback(_retrieved)
                owned.append((index, task, scope))
        lane = _Lane(
            tuple(call for call in calls if not steps[call[0]].is_async),
            self.limit,
            tracked,
        )
        tasks = [task for _index, task, _scope in owned]
        if lane.steps:
            lane.task = asyncio.create_task(
                self._seeded_lane(lane)
                if sharing()
                else run_isolated(partial(self._lane, lane, lane.steps), self.base)
            )
            lane.task.add_done_callback(_retrieved)
            tasks.append(lane.task)
        try:
            await self._watch(owned, lane, tasks)
        finally:
            await _stop(tasks)
        self._apply(calls, self._writes(owned, lane, start, results), start, tracked)

    def _writes(
        self,
        owned: list[tuple[int, asyncio.Task[object], contextvars.Context]],
        lane: _Lane,
        start: contextvars.Context,
        results: list[object],
    ) -> dict[int, tuple[Writes, Values | None]]:
        """Take the result and the writes of every step that finished, by index."""
        writes: dict[int, tuple[Writes, Values | None]] = {}
        for index, task, scope in owned:
            if _failure(task) is None and not task.cancelled():
                results[index] = task.result()
                written = scope_writes(scope, start)
                end = scope.run(read_values, lane.tracked) if written else None
                writes[index] = (written, end)
        done = lane.done
        if lane.task is not None:
            carried = lane.task.get_context()
            for (index, _view), (value, names, end) in zip(
                lane.steps[: len(done)], done, strict=True
            ):
                results[index] = value
                writes[index] = (_carried(carried, names), end)
        return writes

    def _apply(
        self,
        calls: list[tuple[int, dict[str, object]]],
        writes: dict[int, tuple[Writes, Values | None]],
        start: contextvars.Context,
        tracked: tuple[LocalAccessor[Any], ...],
    ) -> None:
        """Restore the writes in declaration order, then settle what steps changed.

        A layer whose steps wrote nothing reads and settles nothing.
        """
        ends: list[Values] = []
        for index, _view in calls:
            if index < self.limit and index in writes:
                written, end = writes[index]
                restore(written)
                if end is not None:
                    ends.append(end)
        if ends:
            settle(tracked, start.run(read_values, tracked), ends)

    async def _watch(
        self,
        owned: list[tuple[int, asyncio.Task[object], contextvars.Context]],
        lane: _Lane,
        tasks: list[asyncio.Task[object]],
    ) -> None:
        """Wait for the tasks, cancelling the async steps after each new failure."""
        pending = set(tasks)
        while pending:
            _finished, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_EXCEPTION
            )
            for index, task, _scope in owned:
                if task.done() and (error := _failure(task)) is not None:
                    self._fail(index, error)
            lane_task = lane.task
            if lane_task is not None and lane_task.done():
                error = _failure(lane_task)
                if error is not None:
                    self._fail(lane.steps[len(lane.done)][0], error)
            lane.stop = self.limit
            for index, task, _scope in owned:
                if index > self.limit:
                    task.cancel()


def _carried(
    carried: contextvars.Context, written: frozenset[contextvars.ContextVar[Any]]
) -> Writes:
    """Return the values `carried` holds for the variables a sync step wrote."""
    return {variable: carried[variable] for variable in written if variable in carried}


class _Lane:
    """The sync steps of one layer, the task running them and what they finished.

    The loop lowers `stop` while the thread runs the steps, which reads it before each.
    """

    __slots__ = ("done", "steps", "stop", "task", "tracked")

    def __init__(
        self,
        steps: tuple[tuple[int, dict[str, object]], ...],
        stop: int,
        tracked: tuple[LocalAccessor[Any], ...],
    ) -> None:
        """Start with no step finished, stopping before any step past `stop`."""
        self.steps = steps
        self.tracked = tracked
        self.done: list[
            tuple[object, frozenset[contextvars.ContextVar[Any]], Values | None]
        ] = []
        self.stop = stop
        self.task: asyncio.Task[object] | None = None


@dataclass(frozen=True, slots=True)
class Phase:
    """The steps one part of a gated render runs, over what earlier parts took.

    `timings` collects how long each step took, when the caller keeps them.
    """

    selected: frozenset[int]
    prior: Mapping[int, object]
    timings: dict[int, float] | None = None


def _phased(
    graph: ContextGraph,
    file_path: Path,
    phase: Phase,
    request: HttpRequest | None,
    url_kwargs: Mapping[str, object],
    dep_cache: dict[str, object],
) -> tuple[_Run, dict[int, object]]:
    """Return the run of the steps `phase` selects and the mapping of its results."""
    base = ResolutionContext.for_page(request, url_kwargs, dep_cache)
    run = _Run(graph, file_path, base)
    return run, run.select(phase)


def run_phase(
    graph: ContextGraph,
    file_path: Path,
    phase: Phase,
    *,
    request: HttpRequest | None,
    url_kwargs: Mapping[str, object],
    dep_cache: dict[str, object],
) -> dict[int, object]:
    """Run the selected steps of `graph` in order and return what each one returned.

    Results merge exactly as `collect` merges them, earlier phases first.
    """
    run, taken = _phased(graph, file_path, phase, request, url_kwargs, dep_cache)
    run.run(Merge(graph, file_path), range(len(graph.steps)), run.base)
    return taken


async def arun_phase(
    graph: ContextGraph,
    file_path: Path,
    phase: Phase,
    *,
    request: HttpRequest | None,
    url_kwargs: Mapping[str, object],
    dep_cache: dict[str, object],
) -> dict[int, object]:
    """Run what `run_phase` runs along the graph, overlapping the async steps."""
    run, taken = _phased(graph, file_path, phase, request, url_kwargs, dep_cache)
    if graph.parallel:
        await run.parallel(graph)
    else:
        await run.serial()
    return taken


def merged(
    graph: ContextGraph, file_path: Path, results: Mapping[int, object]
) -> ContextResult:
    """Return the context `results` merge into, in run order."""
    merge = Merge(graph, file_path)
    for index in sorted(results):
        merge.add(index, results[index])
    return merge.result()


def collect(
    order: RunOrder,
    file_path: Path,
    *,
    request: HttpRequest | None,
    url_kwargs: Mapping[str, object],
    dep_cache: dict[str, object],
    requested_zones: frozenset[str] | None = None,
    groups: int | None = None,
) -> ContextResult:
    """Collect the context of `order` on this thread, every step of it sync.

    A `groups` count runs only that many leading inherited groups, for a shell.
    """
    merge = Merge(order, file_path)
    steps = order.steps
    if steps:
        base = ResolutionContext.for_page(
            request, url_kwargs, dep_cache, merge.inherited
        )
        run = _Run(order, file_path, base, requested_zones, groups)
        run.run(merge, range(len(steps)), base)
    return merge.result()


async def acollect(
    graph: ContextGraph,
    file_path: Path,
    *,
    request: HttpRequest | None,
    url_kwargs: Mapping[str, object],
    dep_cache: dict[str, object],
    requested_zones: frozenset[str] | None = None,
    groups: int | None = None,
) -> ContextResult:
    """Collect the context of `graph`, serially unless a layer overlaps async steps.

    A `groups` count runs only that many leading inherited groups, for a shell.
    """
    base = ResolutionContext.for_page(request, url_kwargs, dep_cache)
    run = _Run(graph, file_path, base, requested_zones, groups)
    if graph.parallel:
        return await run.parallel(graph)
    return await run.serial()


__all__ = ["Phase", "acollect", "arun_phase", "collect", "merged", "run_phase"]
