"""Drive a coroutine graph to completion from a sync view on the request thread."""

import asyncio
import contextvars
import inspect
import os
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from typing import Any

from asgiref.sync import AsyncToSync, SyncToAsync

from next.deps.scope import running_loop
from next.suspense.executors import (
    RequestThreadExecutor,
    background_loop,
    check_asgiref,
    request_executor,
)


type Work[T] = Awaitable[T] | Callable[[], Awaitable[T]]


class _EscapeError(Exception):
    """Carry an interrupt raised inside the coroutine past its task to the caller."""

    def __init__(self, error: BaseException) -> None:
        """Hold the interrupt a bare raise would let out of the background loop."""
        super().__init__(error)
        self.error = error


class _Run[T]:
    """One coroutine on an event loop and the future the request thread awaits."""

    _task: asyncio.Task[T]

    def __init__(self, work: Work[T]) -> None:
        """Hold `work` until `start` creates its task on the loop."""
        self._work = work
        self.outcome: Future[T] = Future()

    def start(
        self, context: contextvars.Context, parents: list[asyncio.Task[Any]] | None
    ) -> None:
        """Create the task in `context` and list it in `parents` while it runs.

        Cancelling the call that awaits this thread makes asgiref cancel the first one.
        """
        task = asyncio.get_running_loop().create_task(self._main(), context=context)
        task.add_done_callback(self._settle)
        self._task = task
        if parents is not None:
            parents.append(task)
            task.add_done_callback(parents.remove)

    def cancel(self) -> None:
        """Cancel the task, which `start` queued ahead of this call on the same loop."""
        self._task.cancel()

    async def _main(self) -> T:
        try:
            awaitable: Awaitable[T] = (
                self._work() if callable(self._work) else self._work
            )
            return await awaitable
        except (KeyboardInterrupt, SystemExit) as error:
            raise _EscapeError(error) from error

    def _settle(self, task: asyncio.Task[T]) -> None:
        if task.cancelled():
            try:
                task.result()
            except asyncio.CancelledError as error:
                self.outcome.set_exception(error)
            return
        failure = task.exception()
        if failure is None:
            self.outcome.set_result(task.result())
        elif isinstance(failure, _EscapeError):
            self.outcome.set_exception(failure.error)
        else:
            self.outcome.set_exception(failure)


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


def _start(run: _Run[Any], context: contextvars.Context) -> asyncio.AbstractEventLoop:
    """Start `run` on the server loop running this thread, else on the background loop.

    On the server loop it is listed where `async_to_sync` lists its task for cancelling.
    """
    loop = server_loop()
    if loop is None:
        return background_loop.call_soon(run.start, context, None)
    parents = getattr(SyncToAsync.threadlocal, "task_context", None)
    loop.call_soon_threadsafe(run.start, context, parents)
    return loop


def _restore_context(context: contextvars.Context) -> None:
    """Copy into the current context every variable `context` holds a new value of."""
    for variable, value in context.items():
        try:
            if variable.get() is not value:
                variable.set(value)
        except LookupError:
            variable.set(value)


def _bind(executor: RequestThreadExecutor | None) -> None:
    AsyncToSync.executors.current = executor


def _drive[T](work: Work[T]) -> T:
    previous: RequestThreadExecutor | None = getattr(
        AsyncToSync.executors, "current", None
    )
    context = contextvars.copy_context()
    run = _Run(work)
    with request_executor() as executor:
        context.run(_bind, executor)
        run.outcome.add_done_callback(lambda _: executor.wake())
        loop = _start(run, context)
        try:
            executor.serve(run.outcome)
        except BaseException:
            loop.call_soon_threadsafe(run.cancel)
            executor.serve(run.outcome)
            raise
        finally:
            try:
                _restore_context(context)
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
        msg = (
            "You cannot drive a coroutine from a thread running an event loop - "
            "await it directly."
        )
        raise RuntimeError(msg)
    check_asgiref()
    return _drive(work)


__all__ = ["drive", "server_loop"]
