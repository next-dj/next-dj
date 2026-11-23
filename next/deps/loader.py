"""The per-request loader that gathers the keys of one request into few batch calls."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Hashable, Mapping, Sequence
from concurrent.futures import Executor, Future, wait
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from functools import partial
from itertools import count
from typing import TYPE_CHECKING, Any, Final, cast, override

from asgiref.sync import (
    AsyncToSync,
    SyncToAsync,
    ThreadSensitiveContext,
    async_to_sync,
    sync_to_async,
)

from next.conf.defer import defer_count, defer_seconds
from next.introspect import (
    callable_name,
    describe_callable,
    is_async_callable,
    is_coroutine_callable,
)
from next.ports import context_propagator_slot, wait_served
from next.tasks import ContextSnapshot, QuitParentExecutor, StealableTask

from .cache import request_memo
from .errors import DependencyCycleError, MissingKeyError
from .runner import SegmentRunner, batch_scope, run_segment, segment_runner
from .scope import _Failure, join_waits, running_loop


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from django.db.models import Model

    from .scope import RequestScope


logger = logging.getLogger(__name__)

_HUB: Final[str] = "next.deps.loader"

_CLOSED: Final[str] = "The request scope is closed, so no batch of it loads this key."

_HALTED: Final[str] = "The batch function %s raised %s, kept as its keys' failure."

type _Route = tuple[object, SegmentRunner, ThreadSensitiveContext | None]

_running: ContextVar[tuple[_Batch, ...]] = ContextVar(
    "next_deps_loader_running", default=()
)

_counted: ContextVar[CountedJob | None] = ContextVar(
    "next_deps_loader_counted", default=None
)


def _current() -> object:
    """Return the executor thread-sensitive calls of the current context go to."""
    return getattr(AsyncToSync.executors, "current", None)


def _route_to(executor: Executor | None) -> None:
    """Send the thread-sensitive calls of the current context to `executor`."""
    AsyncToSync.executors.current = executor


def _route() -> _Route:
    """Return where the current context sends thread-sensitive calls and segments."""
    sensitive = SyncToAsync.thread_sensitive_context.get(None)
    return _current(), segment_runner.get(), sensitive


def _take_route(route: _Route) -> None:
    """Send the thread-sensitive calls and segments of this context on `route`."""
    executor, runner, sensitive = route
    AsyncToSync.executors.current = executor
    segment_runner.set(runner)
    if sensitive is not None:
        SyncToAsync.thread_sensitive_context.set(sensitive)


def _detached(captured: ContextSnapshot) -> ContextSnapshot:
    """Return a copy of `captured` with no route, running batch or counted job."""
    detached = captured.context.copy()
    detached.run(_route_to, None)
    detached.run(_running.set, ())
    detached.run(_counted.set, None)
    detached.run(join_waits.set, None)
    return ContextSnapshot(detached, captured.thread)


class _Refusal(QuitParentExecutor):
    """Refuse every thread-sensitive call of an async batch function on the loop."""

    def __init__(self, batch: Callable[..., object]) -> None:
        """Word the refusal after the batch function `batch`."""
        self.message = (
            f"The async batch function {describe_callable(batch)} of a Loader runs on "
            "the event loop, so it cannot use the Django ORM or "
            "sync_to_async(thread_sensitive=True). Write a batch function that queries "
            "the database as a plain def."
        )

    @override
    def submit[**P, R](
        self, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs
    ) -> Future[R]:
        """Raise, as no thread may serve a call of an async batch function."""
        raise RuntimeError(self.message)


class _Held(threading.local):
    """The sync batches whose call runs on this thread, outermost first."""

    def __init__(self) -> None:
        """Hold no batch on a new thread."""
        self.batches: list[_Batch] = []


_held = _Held()


def _wait(future: Future[Any]) -> None:
    """Block until `future` is done, serving no hop."""
    wait((future,))


def _refuse_cycle(batch: _Batch, key: object) -> None:
    """Raise when a wait on a key of `batch` would wait on the caller itself.

    That is a batch function inside whose call the caller runs, or one this thread runs
    in an outer frame, which no wait here lets return.
    """
    chain: Sequence[_Batch] = _running.get()
    if batch not in chain:
        chain = _held.batches
        if batch not in chain:
            return
    names = [callable_name(held.loader.batch) for held in chain[chain.index(batch) :]]
    raise DependencyCycleError([*names, f"{names[0]}[{key!r}]"])


class _Entry:
    """One key of one request, waiting in a batch or settled with its outcome."""

    __slots__ = ("batch", "failure", "key", "settled", "signal", "value")

    def __init__(self, key: object, batch: _Batch) -> None:
        """Start the key unsettled in `batch`."""
        self.key = key
        self.batch = batch
        self.value: object = None
        self.failure: _Failure | None = None
        self.settled = False
        self.signal: asyncio.Future[None] | None = None

    def settle(self, found: Mapping[object, object] | _Failure) -> None:
        """Record the value `found` holds for the key, or the failure of its batch."""
        if isinstance(found, _Failure):
            self.failure = found
        else:
            try:
                self.value = _pick(found, self.key)
            except MissingKeyError as error:
                self.failure = _Failure(error, error.__traceback__, error.__context__)
        self.settled = True

    def result(self) -> object:
        """Return the value of the key, or raise its memoised error."""
        if self.failure is not None:
            self.failure.rethrow()
        return self.value


class _Batch:
    """The keys one loader gathers in one request until its window closes."""

    __slots__ = (
        "carrier",
        "closed",
        "done",
        "entries",
        "loader",
        "name",
        "routes",
        "settled",
        "started",
        "task",
        "timer",
        "via",
    )

    def __init__(self, loader: Loader[Any, Any], name: str, hub: _Hub) -> None:
        """Open the batch and arm its window on the loop of `hub`."""
        self.loader = loader
        self.name = name
        self.entries: list[_Entry] = []
        self.closed: Future[None] = Future()
        self.done: Future[None] = Future()
        self.settled = False
        self.started = False
        self.routes: dict[object, _Route] = {}
        self.via: object = None
        self.carrier: int | None = None
        self.task: StealableTask[None] = StealableTask(partial(hub.run_here, self))
        self.timer = hub.loop.call_later(loader.window, hub.close, self)

    def keys(self) -> list[object]:
        """Return the keys in the order they joined."""
        return [entry.key for entry in self.entries]


class CountedJob:
    """One job of a request that may add a key, counted until it ends or its deadline.

    It joins a hub only once the first load of the request made one.
    """

    __slots__ = ("deadline", "hub", "joined", "left", "loaders", "timer", "waits")

    def __init__(self, loaders: _Loaders, deadline: float) -> None:
        """Count the job in `loaders` until the monotonic instant `deadline` at most."""
        self.loaders = loaders
        self.deadline = deadline
        self.hub: _Hub | None = None
        self.joined = False
        self.waits = 0
        self.left = False
        self.timer: asyncio.TimerHandle | None = None
        loaders.start(self)

    @contextmanager
    def running(self) -> Iterator[None]:
        """Mark the code of this context as the job, so its waits on a key count."""
        token = _counted.set(self)
        hooked = join_waits.set(partial(_joining, self))
        try:
            yield
        finally:
            join_waits.reset(hooked)
            _counted.reset(token)

    def end(self) -> None:
        """Stop counting the job, as it ended or will never start."""
        self.loaders.stop(self)


class _JobCount:
    """The counted jobs of one request and how many of them wait on a key, on its loop.

    When every counted job waits on a key or left, `flush` closes the open batches.
    """

    __slots__ = ("_checking", "_flush", "_jobs", "_parked", "loop")

    def __init__(
        self, loop: asyncio.AbstractEventLoop, flush: Callable[[], None]
    ) -> None:
        """Count no job yet on `loop`, closing the open batches with `flush`."""
        self.loop = loop
        self._flush = flush
        self._jobs = 0
        self._parked = 0
        self._checking = False

    def post(self, call: Callable[[CountedJob], None], job: CountedJob) -> None:
        """Run `call` for `job` now on the loop thread, else hand it to the loop."""
        if running_loop() is self.loop:
            call(job)
        else:
            self.loop.call_soon_threadsafe(call, job)

    def signal(
        self, call: Callable[[CountedJob], None], job: CountedJob | None
    ) -> None:
        """Hand `call` for the counted job of a thread to the loop, none uncounted."""
        if job is not None:
            self.loop.call_soon_threadsafe(call, job)

    def join(self, job: CountedJob) -> None:
        """Count a job of the request, leaving it uncounted once its deadline passes.

        A job counted before the hub existed may already wait on a key.
        """
        job.joined = True
        self._jobs += 1
        if job.waits:
            self._parked += 1
        delay = max(0.0, job.deadline - time.monotonic())
        job.timer = self.loop.call_later(delay, self.leave, job)

    def park(self, job: CountedJob | None) -> None:
        """Mark `job` waiting on a key, checking after any wait, uncounted ones too."""
        if job is not None:
            job.waits += 1
            if job.waits == 1 and job.joined and not job.left:
                self._parked += 1
        self.nudge()

    def resume(self, job: CountedJob | None) -> None:
        """Mark `job` running again once its last wait on a key ended."""
        if job is None:
            return
        job.waits -= 1
        if not job.waits and job.joined and not job.left:
            self._parked -= 1

    def leave(self, job: CountedJob) -> None:
        """Stop counting a job that returned or ran out of time, once."""
        if job.left:
            return
        job.left = True
        cast("asyncio.TimerHandle", job.timer).cancel()
        self._jobs -= 1
        if job.waits:
            self._parked -= 1
        self._recheck()

    def nudge(self) -> None:
        """Check while a job is counted, as only the window closes a batch of none."""
        if self._jobs:
            self._recheck()

    def _recheck(self) -> None:
        """Check on the next loop turn, so the tasks ready now add their keys first."""
        if not self._checking:
            self._checking = True
            self.loop.call_soon(self._check)

    def _check(self) -> None:
        """Close the open batches once every counted job waits on a key or left."""
        self._checking = False
        if self._parked == self._jobs:
            self._flush()


class _Hub:
    """The open batches and key memo of one request, written on its loop.

    Threads read the memo and hand each write to the loop with `call_soon_threadsafe`.
    """

    __slots__ = (
        "_admitting",
        "_captured",
        "_drivers",
        "_lock",
        "_memo",
        "_open",
        "_serial",
        "calls",
        "jobs",
        "loop",
        "scope",
        "shut",
    )

    def __init__(
        self,
        scope: RequestScope,
        loop: asyncio.AbstractEventLoop,
        captured: ContextSnapshot,
    ) -> None:
        """Bind the hub to the root scope, the loop and the context batches run in."""
        self.scope = scope
        self.loop = loop
        self.shut = False
        self._captured = _detached(captured)
        self._open: dict[Loader[Any, Any], _Batch] = {}
        self._memo: dict[Loader[Any, Any], dict[object, _Entry]] = {}
        self._drivers: set[asyncio.Task[None]] = set()
        self.calls: set[asyncio.Future[Any]] = set()
        self._admitting: set[Future[_Entry]] = set()
        self._lock = threading.Lock()
        self._serial = count()
        self.jobs = _JobCount(loop, self.flush)

    def adopt(self, captured: ContextSnapshot) -> None:
        """Run the batches started from now on in the context of `captured`."""
        self._captured = _detached(captured)

    def cached(self, loader: Loader[Any, Any], key: object) -> _Entry | None:
        """Return the entry of `key`, read from any thread, or None before it joins."""
        memo = self._memo.get(loader)
        return None if memo is None else memo.get(key)

    def enter(self, loader: Loader[Any, Any], key: object) -> _Entry:
        """Return the entry of `key`, adding it to the open batch on a miss."""
        memo = self._memo.setdefault(loader, {})
        entry = memo.get(key)
        if entry is not None:
            return entry
        if self.shut:
            raise RuntimeError(_CLOSED)
        batch = self._open.get(loader)
        if batch is None:
            name = f"{_HUB}#{next(self._serial)}"
            batch = self._open[loader] = _Batch(loader, name, self)
            self.jobs.nudge()
        entry = memo[key] = _Entry(key, batch)
        batch.entries.append(entry)
        if len(batch.entries) >= loader.max_batch:
            self.close(batch)
        return entry

    def admission(self, loader: Loader[Any, Any], key: object) -> Future[_Entry]:
        """Hand `key` to the loop for a thread, the entry coming back on the future.

        The future is tracked until the loop answers it, so a closing scope fails it.
        """
        admitted: Future[_Entry] = Future()
        with self._lock:
            if self.shut:
                raise RuntimeError(_CLOSED)
            self._admitting.add(admitted)
        self.loop.call_soon_threadsafe(self.admit, loader, key, admitted)
        return admitted

    def admit(
        self, loader: Loader[Any, Any], key: object, admitted: Future[_Entry]
    ) -> None:
        """Enter `key` for a thread, resolving `admitted` whatever the entry raises."""
        with self._lock:
            self._admitting.discard(admitted)
        if admitted.done():
            return
        try:
            entry = self.enter(loader, key)
        except BaseException as error:
            admitted.set_exception(error)
            if not isinstance(error, Exception):
                raise
        else:
            admitted.set_result(entry)

    def flush(self) -> None:
        """Close every open batch, as no running job of the request can add a key."""
        for batch in list(self._open.values()):
            self.close(batch)

    def counted(self) -> CountedJob | None:
        """Return the job counted in the current context when it runs in this hub."""
        job = _counted.get()
        return job if job is not None and job.hub is self else None

    def close(self, batch: _Batch) -> None:
        """Freeze the keys of `batch`, starting it here when it runs on the loop.

        An async batch always does. A sync one does once a loop task waits on it, its
        call queued on that task's segment runner, else a waiting thread runs it.
        """
        self._freeze(batch)
        if batch.loader.awaits or batch.routes:
            self.start(batch)

    def wait_on(self, batch: _Batch, route: _Route) -> object:
        """Hold the route of a loop waiter on `batch` and return the token it drops by.

        A closed batch starts at once.
        """
        waiter = object()
        batch.routes[waiter] = route
        if batch.closed.done():
            self.start(batch)
        return waiter

    def drop(self, batch: _Batch, waiter: object) -> None:
        """Forget the route of a loop waiter that stopped waiting on `batch`."""
        del batch.routes[waiter]

    def start(self, batch: _Batch) -> None:
        """Run `batch` as a task the scope owns, which no cancelled waiter stops.

        A sync batch takes the route of its earliest waiter, an async one refuses hops.
        """
        if batch.started:
            return
        batch.started = True
        captured = self._captured
        restored = captured.get()
        context = (captured.context if restored is None else restored).copy()
        refusal = batch.loader.refusal
        if refusal is None:
            batch.via, route = next(iter(batch.routes.items()))
            context.run(_take_route, route)
        else:
            context.run(_route_to, refusal)
            context.run(_running.set, (batch,))
        carrying = captured if restored is None and refusal is not None else None
        driver = self.loop.create_task(self._drive(batch, carrying), context=context)
        self._drivers.add(driver)
        driver.add_done_callback(self._drivers.discard)
        if refusal is None:
            self._track(driver)

    def _track(self, call: asyncio.Future[Any]) -> None:
        """Hold `call` among the sync calls of the request until it ends."""
        self.calls.add(call)
        call.add_done_callback(self.calls.discard)

    def shut_down(self) -> None:
        """Fail every key no batch has answered and wake its waiters, threads included.

        The scope calls it on close, before it cancels the batch calls still running. A
        sync batch a thread runs already settles once that call returns.
        """
        with self._lock:
            self.shut = True
            admitting = list(self._admitting)
            self._admitting.clear()
        for admitted in admitting:
            admitted.set_exception(RuntimeError(_CLOSED))
        for batch in list(self._open.values()):
            self._freeze(batch)
        entries = [entry for memo in self._memo.values() for entry in memo.values()]
        for batch in {entry.batch for entry in entries}:
            self._abandon(batch, RuntimeError(_CLOSED))
            if batch.settled:
                self._publish(batch)

    def run_here(self, batch: _Batch) -> None:
        """Call the sync batch function in the scope context on the claiming thread.

        A segment runner carried that context here, any other claimant restores it.
        """
        if batch.carrier == threading.get_ident():
            context = copy_context()
        else:
            context = context_propagator_slot.get().restore(self._captured).copy()
        context.run(_running.set, (*_running.get(), batch))
        held = _held.batches
        held.append(batch)
        try:
            context.run(self._call, batch)
        finally:
            held.pop()

    def _freeze(self, batch: _Batch) -> None:
        """Take `batch` off the open ones and wake the threads awaiting its window."""
        del self._open[batch.loader]
        batch.timer.cancel()
        batch.closed.set_result(None)

    async def _drive(self, batch: _Batch, carrying: ContextSnapshot | None) -> None:
        """Run `batch` through the scope, failing its keys when the run fails.

        `carrying` reaches the loop first. An interrupt is logged, as none awaits it.
        """
        try:
            if carrying is None:
                await self._resolve(batch)
            else:
                await self._carried(batch, carrying)
        except BaseException as error:
            if isinstance(error, Exception) and self._unserved(batch):
                self._reroute(batch)
                return
            self._abandon(batch, error)
            if isinstance(error, asyncio.CancelledError):
                raise
            if not isinstance(error, Exception):
                name = describe_callable(batch.loader.batch)
                logger.exception(_HALTED, name, type(error).__name__)

    async def _resolve(self, batch: _Batch) -> None:
        await self.scope.resolve(batch.name, partial(self._execute, batch))

    async def _carried(self, batch: _Batch, captured: ContextSnapshot) -> None:
        """Resolve an async `batch` in `captured` restored on this loop.

        This task runs in the captured context already, so a moved one needs its own.
        """
        context = await context_propagator_slot.get().arestore(captured)
        if context is captured.context:
            await self._resolve(batch)
            return
        refused = context.copy()
        refused.run(_route_to, batch.loader.refusal)
        refused.run(_running.set, (batch,))
        await self.loop.create_task(self._resolve(batch), context=refused)

    def _unserved(self, batch: _Batch) -> bool:
        """Whether the call of `batch` failed on the route of a waiter that has gone.

        Its executor may have closed with it, so the error is no outcome of the keys.
        """
        return not (self.shut or batch.settled) and batch.via not in batch.routes

    def _reroute(self, batch: _Batch) -> None:
        """Start `batch` again under a new name on a live route, else leave it unrouted.

        An unrouted batch waits for its next waiter, which runs it as it always does.
        """
        batch.started = False
        batch.name = f"{_HUB}#{next(self._serial)}"
        if batch.routes:
            self.start(batch)

    async def _execute(self, batch: _Batch) -> None:
        """Await an async batch here, or queue a sync one on the segment runner.

        The sync call is tracked until it returns, as no cancel of this wait stops it.
        """
        if batch.loader.awaits:
            await self._run_async(batch)
            return
        call = asyncio.ensure_future(run_segment(partial(self._on_segment, batch)))
        self._track(call)
        await asyncio.wait((call,))
        call.result()

    def _on_segment(self, batch: _Batch) -> bool:
        """Run `batch` on the thread its segment runner carried the batch context to."""
        batch.carrier = threading.get_ident()
        try:
            return batch.task.run()
        finally:
            batch.carrier = None

    def _call(self, batch: _Batch) -> None:
        """Call the sync batch function and settle every key with what it returned."""
        keys = batch.keys()
        try:
            found = _mapped(keys, batch.loader.batch(keys))
        except BaseException as error:
            self._settle(batch, error)
            if not isinstance(error, Exception):
                raise
        else:
            self._settle(batch, found)

    async def _run_async(self, batch: _Batch) -> None:
        """Await the async batch function and settle every key with what it returned."""
        keys = batch.keys()
        try:
            result = await cast("Awaitable[object]", batch.loader.batch(keys))
            found = _mapped(keys, result)
        except BaseException as error:
            self._settle(batch, error)
            if not isinstance(error, Exception):
                raise
        else:
            self._settle(batch, found)

    def _abandon(self, batch: _Batch, error: BaseException) -> None:
        """Fail every key of `batch` with `error`, unless a thread runs its call."""
        if batch.loader.awaits or batch.task.claim():
            self._settle(batch, error)

    def _settle(
        self, batch: _Batch, found: Mapping[object, object] | BaseException
    ) -> None:
        """Write the outcome of every key of `batch` once, then wake its waiters."""
        if batch.settled:
            return
        batch.settled = True
        outcome = (
            _Failure(found, found.__traceback__, found.__context__)
            if isinstance(found, BaseException)
            else found
        )
        for entry in batch.entries:
            entry.settle(outcome)
        batch.done.set_result(None)
        self.loop.call_soon_threadsafe(self._publish, batch)

    def _publish(self, batch: _Batch) -> None:
        """Wake the loop tasks waiting on the keys of a settled batch, once each."""
        for entry in batch.entries:
            signal = entry.signal
            if signal is not None and not signal.done():
                signal.set_result(None)


class Loader[K: Hashable, V]:
    """Gather the keys one request loads into one call of `batch` per window.

    One loader serves every request, as each keeps its batches and memo on its scope.
    """

    __slots__ = ("_max_batch", "_window", "awaits", "batch", "refusal")

    def __init__(
        self,
        batch: Callable[
            [Sequence[K]],
            Mapping[K, V] | Sequence[V] | Awaitable[Mapping[K, V] | Sequence[V]],
        ],
        *,
        max_batch: int | None = None,
        window: float | None = None,
    ) -> None:
        """Hold the batch function, sync or `async def`, and the limits of a batch.

        An `async def` one serves native async I/O, as it may not hop to a thread, so an
        ORM batch function is a plain def. A `None` limit follows its `DEFER` setting.
        """
        if max_batch is not None and max_batch < 1:
            msg = f"A loader takes a max_batch of at least 1, got {max_batch}."
            raise ValueError(msg)
        awaits = is_coroutine_callable(batch)
        if not awaits and is_async_callable(batch):
            msg = (
                "A loader takes a sync or an async def batch function that returns "
                "its values, and an async generator yields them instead."
            )
            raise TypeError(msg)
        self.batch = batch
        self.awaits = awaits
        self.refusal = _Refusal(batch) if awaits else None
        self._max_batch = max_batch
        self._window = window

    @property
    def max_batch(self) -> int:
        """Return the most keys one call of the batch function receives."""
        if self._max_batch is None:
            return defer_count("LOADER_MAX_BATCH")
        return self._max_batch

    @property
    def window(self) -> float:
        """Return the seconds a batch stays open after its first key."""
        if self._window is None:
            return defer_seconds("BATCH_WINDOW")
        return self._window

    async def load(self, key: K) -> V:
        """Return the value of `key`, joining the open batch of the current request."""
        key = self._keyed(key)
        scope = _request_scope()
        if scope is None:
            return await self._alone(key)
        loop = asyncio.get_running_loop()
        own = scope.loop
        hub = _hub(scope, loop if own is None else own)
        if hub.loop is not loop:
            return await sync_to_async(self.load_sync, thread_sensitive=True)(key)
        entry = hub.enter(self, key)
        if not entry.settled:
            batch = entry.batch
            _refuse_cycle(batch, key)
            self._refuse_refused()
            waiter = hub.wait_on(batch, _route())
            if entry.signal is None:
                entry.signal = loop.create_future()
            job = hub.counted()
            jobs = hub.jobs
            jobs.park(job)
            try:
                await asyncio.shield(entry.signal)
            finally:
                jobs.resume(job)
                hub.drop(batch, waiter)
        return cast("V", entry.result())

    def load_sync(self, key: K) -> V:
        """Return the value of `key` on a thread, running a sync batch here if idle.

        Nothing an async batch waits for needs this thread, as it makes no hop to one. A
        thread inside a sync batch call serves no hop, so none waits on its outer frame.
        """
        _refuse_loop()
        key = self._keyed(key)
        scope = _request_scope()
        hub = None if scope is None else _thread_hub(scope)
        if hub is None:
            return self._alone_sync(key)
        entry = hub.cached(self, key)
        waiting = _wait if _held.batches else wait_served
        if entry is None:
            admitted = hub.admission(self, key)
            waiting(admitted)
            entry = admitted.result()
        if not entry.settled:
            batch = entry.batch
            _refuse_cycle(batch, key)
            job = hub.counted()
            jobs = hub.jobs
            jobs.signal(jobs.park, job)
            try:
                waiting(batch.closed)
                if self.awaits or not batch.task.run():
                    waiting(batch.done)
            finally:
                jobs.signal(jobs.resume, job)
        return cast("V", entry.result())

    def _refuse_refused(self) -> None:
        """Raise when an async batch function waits on a sync batch to query for it."""
        refusal = _current()
        if not self.awaits and isinstance(refusal, _Refusal):
            raise RuntimeError(refusal.message)

    def _keyed(self, key: K) -> K:
        """Return `key` as the memo holds it, hashed so a bad key fails its caller."""
        hash(key)
        return key

    async def _alone(self, key: K) -> V:
        """Call the batch function with `key` alone, a sync one through the runner."""
        if not self.awaits:
            return await run_segment(partial(self._alone_sync, key))
        keys = [key]
        context = copy_context()
        context.run(_route_to, self.refusal)
        fetch = asyncio.get_running_loop().create_task(
            self._fetch(keys), context=context
        )
        return cast("V", _pick(_mapped(keys, await fetch), key))

    async def _fetch(self, keys: list[K]) -> object:
        """Await the async batch function, as a task whose hops are refused."""
        return await cast("Awaitable[object]", self.batch(keys))

    def _alone_sync(self, key: K) -> V:
        """Call the batch function with `key` alone from a thread."""
        if self.awaits:
            return async_to_sync(self._alone)(key)
        keys = [key]
        return cast("V", _pick(_mapped(keys, self.batch(keys)), key))


def _mapped(keys: Sequence[object], result: object) -> Mapping[object, object]:
    """Return a batch result keyed by key, from a mapping or values in key order."""
    if isinstance(result, Mapping):
        return result
    got = type(result).__name__
    if isinstance(result, Sequence) and not isinstance(result, str | bytes | bytearray):
        if len(result) == len(keys):
            return dict(zip(keys, result, strict=True))
        got = f"{got} of {len(result)}"
    msg = (
        "A batch function returns a mapping of key to value or a sequence of "
        f"{len(keys)} values in key order, got {got}."
    )
    raise TypeError(msg)


def _pick(found: Mapping[object, object], key: object) -> object:
    """Return the value `found` holds for `key`, or raise `MissingKeyError`."""
    try:
        return found[key]
    except KeyError:
        raise MissingKeyError(key) from None


def _request_scope() -> RequestScope | None:
    """Return the scope the loads of the current context batch in, None outside one."""
    memo = request_memo.get()
    return batch_scope.get() if memo is None else memo.scope


def _refuse_loop() -> None:
    """Raise on a thread that runs an event loop, which a blocking wait would stall."""
    if running_loop() is not None:
        msg = "load_sync() blocks, so it cannot run on an event loop. Await load()."
        raise RuntimeError(msg)


class _Loaders:
    """The loader state of one request, its hub made by the first load alone.

    Until then jobs are counted here off the loop, and the hub takes over the rest.
    """

    __slots__ = ("_captured", "_jobs", "_lock", "hub", "root")

    def __init__(self, root: RequestScope) -> None:
        """Hold no hub, no context and no job yet."""
        self.root = root
        self.hub: _Hub | None = None
        self._captured: ContextSnapshot | None = None
        self._jobs: set[CountedJob] = set()
        self._lock = threading.Lock()

    def adopt(self, captured: ContextSnapshot) -> None:
        """Run the batches started from now on in the context of `captured`."""
        with self._lock:
            hub = self.hub
            if hub is None:
                self._captured = captured
                return
        hub.adopt(captured)

    def made(self, loop: asyncio.AbstractEventLoop) -> _Hub:
        """Return the hub, making it on `loop` with the jobs counted so far."""
        hub = self.hub
        if hub is not None:
            return hub
        with self._lock:
            hub = self.hub
            if hub is None:
                captured = self._captured
                if captured is None:
                    captured = ContextSnapshot(copy_context())
                hub = _Hub(self.root, loop, captured)
                for job in self._jobs:
                    job.hub = hub
                    hub.jobs.post(hub.jobs.join, job)
                self._jobs.clear()
                self.root.on_close(hub.shut_down)
                self.hub = hub
        return hub

    def start(self, job: CountedJob) -> None:
        """Count `job` in the hub, or keep it for the hub the first load makes."""
        with self._lock:
            hub = self.hub
            if hub is None:
                self._jobs.add(job)
                return
            job.hub = hub
        hub.jobs.post(hub.jobs.join, job)

    def stop(self, job: CountedJob) -> None:
        """Stop counting `job`, which the hub learns after it learned of its start."""
        with self._lock:
            hub = job.hub
            if hub is None:
                self._jobs.discard(job)
                return
        hub.jobs.post(hub.jobs.leave, job)


def _loaders(scope: RequestScope) -> _Loaders:
    """Return the loader state of the request, which its root scope holds for all."""
    return scope.slot(_HUB, _Loaders)


def _hub(scope: RequestScope, loop: asyncio.AbstractEventLoop) -> _Hub:
    """Return the batches of the request, made on `loop` by the first load."""
    return _loaders(scope).made(loop)


def _thread_hub(scope: RequestScope) -> _Hub | None:
    """Return the batches of the request for a thread, None before the loop is known."""
    loop = scope.loop
    return None if loop is None else _hub(scope, loop)


def open_loaders(scope: RequestScope, captured: ContextSnapshot) -> None:
    """Run the batches of the request on `scope` in the context of `captured`.

    Without it they run in the context of the first load, its deadline included.
    """
    if scope.loop is not None:
        _loaders(scope).adopt(captured)


def count_job(scope: RequestScope | None, deadline: float) -> CountedJob | None:
    """Count a job of the request from now on, None outside a scope bound to a loop.

    The open batches of the request close once every counted job waits on a key or left.
    """
    if scope is None or scope.loop is None:
        return None
    return CountedJob(_loaders(scope), deadline)


@contextmanager
def _joining(job: CountedJob, root: RequestScope) -> Iterator[None]:
    """Count `job` waiting while it joins a key its own request computes elsewhere.

    It runs on the loop, and a wait before the hub exists stays on the job for the hub.
    """
    if root is not job.loaders.root:
        yield
        return
    hub = job.hub
    if hub is None:
        job.waits += 1
    else:
        hub.jobs.park(job)
    try:
        yield
    finally:
        if job.hub is None:
            job.waits -= 1
        else:
            job.hub.jobs.resume(job)


def drain_loaders(scope: RequestScope) -> set[asyncio.Future[Any]]:
    """Close every open batch of the request and return its sync calls still running.

    Called on the loop of the request once its jobs ended. A server joins the request
    thread such a call may run on, so a response waits for them first.
    """
    hub = _loaders(scope).hub
    if hub is None or hub.loop is not asyncio.get_running_loop():
        return set()
    hub.flush()
    return {call for call in hub.calls if not call.done()}


class _RowLoader[M: Model](Loader[Hashable, M]):
    """The row loader of one model, its keys normalised by the primary key field."""

    __slots__ = ("_to_python",)

    def __init__(self, model: type[M]) -> None:
        """Batch the rows of `model` through its default manager."""
        super().__init__(partial(_fetch_rows, model))
        self._to_python = model._meta.pk.to_python

    @override
    def _keyed(self, key: Hashable) -> Hashable:
        """Return `key` as the primary key field reads it, so "5" and 5 name one row."""
        pk = cast("Hashable", self._to_python(key))
        hash(pk)
        return pk


_row_loaders: dict[type[Model], Loader[Hashable, Any]] = {}


def rows[M: Model](model: type[M]) -> Loader[Hashable, M]:
    """Return the loader of `model` rows by primary key, one `pk__in` query a batch.

    It reads the default manager with no access scoping, unlike `DObject`.
    """
    loader = _row_loaders.get(model)
    if loader is None:
        loader = _row_loaders.setdefault(model, _RowLoader(model))
    return cast("Loader[Hashable, M]", loader)


def _fetch_rows[M: Model](
    model: type[M], keys: Sequence[Hashable]
) -> dict[Hashable, M]:
    """Fetch the rows of `model` at `keys` in one query, keyed by primary key."""
    return {row.pk: row for row in model._default_manager.filter(pk__in=keys)}


__all__ = ["CountedJob", "Loader", "count_job", "drain_loaders", "open_loaders", "rows"]
