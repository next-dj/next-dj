"""Drive coroutines from sync views and restore context snapshots across threads."""

import asyncio
import contextvars
import inspect
import os
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, InvalidStateError, ThreadPoolExecutor
from contextlib import suppress
from functools import partial
from typing import Any

from asgiref.local import Local
from asgiref.sync import AsyncToSync, SyncToAsync

from next.defer import remaining
from next.deps.scope import running_loop
from next.ports import ContextPropagator, context_propagator_slot
from next.suspense.executors import (
    RequestThreadExecutor,
    background_loop,
    check_asgiref,
    request_executor,
)
from next.tasks import ContextSnapshot
from next.utils import after_fork


type Work[T] = Awaitable[T] | Callable[[], Awaitable[T]]

_ON_LOOP = (
    "You cannot drive a coroutine from a thread running an event loop - "
    "await it directly."
)
_RESTORE_ON_LOOP = (
    "You cannot restore a context onto a thread running an event loop - await it there."
)
_LATE = "The deadline passed before the snapshot was restored on this thread."


def _bind(executor: RequestThreadExecutor | None) -> None:
    AsyncToSync.executors.current = executor


def _arrive(settle: Callable[[contextvars.Context], object]) -> None:
    """Hand the context the hop carried onto this thread to what waits on it."""
    with suppress(InvalidStateError):
        settle(contextvars.copy_context())


_ARRIVE = SyncToAsync(_arrive, thread_sensitive=True)


async def _arrive_on(
    executor: RequestThreadExecutor, settle: Callable[[contextvars.Context], object]
) -> None:
    """Hop onto the thread `executor` serves, which hands `settle` the context there."""
    _bind(executor)
    await _ARRIVE(settle)


def server_loop() -> asyncio.AbstractEventLoop | None:
    """Return the running server loop whose `sync_to_async` call runs this thread.

    It reads the markers `async_to_sync` reads, minus the background loop a hop marks.
    """
    markers = SyncToAsync.threadlocal
    loop: asyncio.AbstractEventLoop | None = getattr(markers, "main_event_loop", None)
    if (
        loop is None
        or loop is background_loop.current()
        or getattr(markers, "main_event_loop_pid", None) != os.getpid()
        or not loop.is_running()
    ):
        return None
    return loop


def _launch(
    begin: Callable[[list[asyncio.Task[Any]] | None], object],
) -> asyncio.AbstractEventLoop:
    """Call `begin` on the server loop running this thread, else on the background one.

    On the server loop it gets the list `async_to_sync` cancels its own tasks through.
    """
    loop = server_loop()
    if loop is None:
        return background_loop.call_soon(begin, None)
    loop.call_soon_threadsafe(
        begin, getattr(SyncToAsync.threadlocal, "task_context", None)
    )
    return loop


def _listed(task: asyncio.Task[Any], parents: list[asyncio.Task[Any]] | None) -> None:
    """List `task` in `parents` while it runs, so cancelling the caller cancels it."""
    if parents is not None:
        parents.append(task)
        task.add_done_callback(parents.remove)


def _carry(source: contextvars.Context) -> None:
    """Set in the current context each value `source` holds, compared by identity."""
    for variable, value in source.items():
        try:
            if variable.get() is not value:
                variable.set(value)
        except LookupError:
            variable.set(value)


class _Run[T]:
    """One coroutine on an event loop and the future the request thread awaits.

    The bound propagator restores its context, and a moved one goes back by a hop.
    """

    _task: asyncio.Task[None]

    def __init__(
        self,
        work: Work[T],
        executor: RequestThreadExecutor,
        propagator: ContextPropagator,
    ) -> None:
        """Hold `work`, the request thread executor and the propagator until `start`."""
        self._work = work
        self._executor = executor
        self._propagator = propagator
        self.captured = ContextSnapshot(contextvars.copy_context())
        self.outcome: Future[T] = Future()
        self.final: ContextSnapshot | None = None

    def start(self, parents: list[asyncio.Task[Any]] | None) -> None:
        """Create the task in the captured context and list it in `parents` meanwhile.

        Cancelling the call that awaits this thread makes asgiref cancel the first one.
        """
        loop = asyncio.get_running_loop()
        task = loop.create_task(self._main(), context=self.captured.context)
        self._task = task
        _listed(task, parents)

    def cancel(self) -> None:
        """Cancel the task, which `start` queued ahead of this call on the same loop."""
        self._task.cancel()

    async def _main(self) -> None:
        """Run the work in the restored context, in this task if nothing moved."""
        captured = self.captured
        try:
            context = await self._propagator.arestore(captured)
        except BaseException as error:
            self.outcome.set_exception(error)
            if isinstance(error, asyncio.CancelledError):
                raise
            return
        self.final = final = ContextSnapshot(context)
        if context is captured.context:
            await self._body(None)
            return
        loop = asyncio.get_running_loop()
        await loop.create_task(self._body(final), context=context)

    async def _body(self, final: ContextSnapshot | None) -> None:
        """Await the work and settle its outcome, by a hop keeping `final` if it moved.

        A cancel is raised again once settled, an interrupt never leaves the task.
        """
        _bind(self._executor)
        try:
            awaitable = self._work() if callable(self._work) else self._work
            result = await awaitable
        except BaseException as error:
            await self._settle(partial(self.outcome.set_exception, error), final)
            if isinstance(error, asyncio.CancelledError):
                raise
            return
        await self._settle(partial(self.outcome.set_result, result), final)

    async def _settle(
        self, settle: Callable[[], object], final: ContextSnapshot | None
    ) -> None:
        """Settle the outcome in the hop that keeps `final`, else here if it failed.

        Without a kept copy the request thread carries the context back by itself.
        """
        if final is None:
            settle()
            return
        try:
            await _arrive_on(self._executor, partial(_handed, final, settle))
        except BaseException as error:
            with suppress(InvalidStateError):
                settle()
            if isinstance(error, asyncio.CancelledError):
                raise


def _handed(
    final: ContextSnapshot, settle: Callable[[], object], context: contextvars.Context
) -> None:
    """Keep the context a hop carried onto the request thread, then settle."""
    final.keep(context)
    settle()


def _drive[T](work: Work[T], propagator: ContextPropagator) -> T:
    previous: RequestThreadExecutor | None = getattr(
        AsyncToSync.executors, "current", None
    )
    with request_executor() as executor:
        run = _Run(work, executor, propagator)
        run.outcome.add_done_callback(lambda _: executor.wake())
        loop = _launch(run.start)
        try:
            executor.serve(run.outcome)
        except BaseException:
            loop.call_soon_threadsafe(run.cancel)
            executor.serve(run.outcome)
            raise
        finally:
            try:
                if run.final is not None:
                    _carry(propagator.restore(run.final))
            finally:
                _bind(previous)
    return run.outcome.result()


def drive[T](work: Work[T]) -> T:
    """Run `work`, a coroutine or a factory of one, to its result from sync code.

    Thread-sensitive hops run on the calling thread, also while it waits in a hop.
    """
    if running_loop() is not None:
        if inspect.iscoroutine(work):
            work.close()
        raise RuntimeError(_ON_LOOP)
    check_asgiref()
    return _drive(work, context_propagator_slot.get())


def _idle() -> None:
    """Do nothing, as the hop that carries a task needs no work on its thread."""


class _Carrier:
    """The one thread the hops that carry a task onto its loop pass through.

    It runs no work, so a busy default executor of the loop never holds a carry up.
    """

    __slots__ = ("hop",)

    def __init__(self) -> None:
        """Start without a thread until the first hop needs one."""
        self.forget()

    def forget(self) -> None:
        """Drop the executor, as a forked child inherits no thread."""
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="next-carry")
        self.hop = SyncToAsync(_idle, thread_sensitive=False, executor=executor)


_carrier = _Carrier()
after_fork(_carrier.forget)


async def _landed() -> contextvars.Context:
    """Return the context of this task once a hop carried it onto the loop."""
    await _carrier.hop()
    return contextvars.copy_context()


class BridgePropagator:
    """Restore a context snapshot through the asgiref hops that move `Local` values.

    `plain` means a context shows its `Local` values on any thread, so nothing moves.
    """

    __slots__ = ("plain",)

    def __init__(self, *, plain: bool) -> None:
        """Hold whether a context already carries its `Local` values by itself."""
        self.plain = plain

    def restore(self, captured: ContextSnapshot, /) -> contextvars.Context:
        """Return `captured` restored on this thread by a hop from a loop, kept after.

        It waits until the current deadline at most and refuses a thread running a loop.
        """
        found = captured.get()
        if found is not None:
            return found
        if self.plain:
            return captured.context
        if running_loop() is not None:
            raise RuntimeError(_RESTORE_ON_LOOP)
        arrived: Future[contextvars.Context] = Future()
        with request_executor() as executor:
            arrived.add_done_callback(lambda _: executor.wake())
            context = captured.context.copy()
            _launch(partial(_begin, executor, arrived, context))
            executor.serve(arrived, remaining())
        if not arrived.done():
            raise TimeoutError(_LATE)
        return captured.keep(arrived.result())

    async def arestore(self, captured: ContextSnapshot, /) -> contextvars.Context:
        """Return `captured` restored on the running loop by one hop for every caller.

        A cancelled caller leaves the hop running for the others.
        """
        found = captured.get()
        if found is not None:
            return found
        if self.plain:
            return captured.context
        landing = captured.landing(_landed)
        await asyncio.wait((landing,))
        return captured.keep(landing.result())


async def _arrival(
    executor: RequestThreadExecutor, arrived: Future[contextvars.Context]
) -> None:
    """Carry this task onto the thread `executor` serves, a failure reaching it too."""
    try:
        await _arrive_on(executor, arrived.set_result)
    except BaseException as error:
        with suppress(InvalidStateError):
            arrived.set_exception(error)
        if isinstance(error, asyncio.CancelledError):
            raise


def _begin(
    executor: RequestThreadExecutor,
    arrived: Future[contextvars.Context],
    context: contextvars.Context,
    parents: list[asyncio.Task[Any]] | None,
) -> None:
    """Start the hop that carries `context` onto the thread `executor` serves."""
    task = asyncio.get_running_loop().create_task(
        _arrival(executor, arrived), context=context
    )
    _listed(task, parents)


def contexts_carry_locals() -> bool:
    """Whether a `Local` value set in a context reads through it on another thread.

    asgiref before 3.12 keeps the value in the context itself, so no hop moves it.
    """
    local = Local()
    context = contextvars.Context()
    marker = object()
    context.run(setattr, local, "value", marker)
    seen: list[object] = []
    reader = threading.Thread(
        target=lambda: seen.append(context.run(getattr, local, "value", None))
    )
    reader.start()
    reader.join()
    return seen == [marker]


__all__ = ["BridgePropagator", "contexts_carry_locals", "drive", "server_loop"]
