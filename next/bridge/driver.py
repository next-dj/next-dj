"""Drive a coroutine to its result from sync code, serving its hops on the caller."""

import asyncio
import contextvars
import inspect
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, InvalidStateError
from contextlib import suppress
from functools import partial
from typing import Any

from .context import ContextSnapshot
from .hops import check_asgiref, current_executor, route_to
from .loop import launch, listed, running_loop
from .request import RequestThreadExecutor, arrive_on, request_executor


type Work[T] = Awaitable[T] | Callable[[], Awaitable[T]]

_ON_LOOP = (
    "You cannot drive a coroutine from a thread running an event loop - "
    "await it directly."
)


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

    The snapshot restores its context there, and the outcome comes back by a hop.
    """

    _task: asyncio.Task[None]

    def __init__(self, work: Work[T], executor: RequestThreadExecutor) -> None:
        """Hold `work` and the request thread executor until `start`."""
        self._work = work
        self._executor = executor
        self.captured = ContextSnapshot(contextvars.copy_context())
        self.outcome: Future[T] = Future()
        self.returned: contextvars.Context | None = None

    def start(self, parents: list[asyncio.Task[Any]] | None) -> None:
        """Create the task in an empty context and list it in `parents` meanwhile.

        Cancelling the call that awaits this thread makes asgiref cancel the first one.
        """
        loop = asyncio.get_running_loop()
        task = loop.create_task(self._main(), context=contextvars.Context())
        self._task = task
        listed(task, parents)

    def cancel(self) -> None:
        """Cancel the task, which `start` queued ahead of this call on the same loop."""
        self._task.cancel()

    async def _main(self) -> None:
        """Run the work as a task of its own in the context restored on this loop."""
        try:
            context = await self.captured.arestore()
        except BaseException as error:
            self.outcome.set_exception(error)
            if isinstance(error, asyncio.CancelledError):
                raise
            return
        loop = asyncio.get_running_loop()
        await loop.create_task(self._body(), context=context)

    async def _body(self) -> None:
        """Await the work and settle its outcome by the hop that hands the context back.

        A cancel is raised again once settled, an interrupt never leaves the task.
        """
        route_to(self._executor)
        try:
            awaitable = self._work() if callable(self._work) else self._work
            result = await awaitable
        except BaseException as error:
            await self._settle(partial(self.outcome.set_exception, error))
            if isinstance(error, asyncio.CancelledError):
                raise
            return
        await self._settle(partial(self.outcome.set_result, result))

    async def _settle(self, settle: Callable[[], object]) -> None:
        """Settle the outcome in the hop that hands the context back, else here.

        A failed hop hands back nothing, so the request thread keeps its own values.
        """
        try:
            await arrive_on(self._executor, partial(_handed, self, settle))
        except BaseException as error:
            with suppress(InvalidStateError):
                settle()
            if isinstance(error, asyncio.CancelledError):
                raise


def _handed(
    run: _Run[Any], settle: Callable[[], object], context: contextvars.Context
) -> None:
    """Keep the context a hop carried onto the request thread, then settle."""
    run.returned = context
    settle()


def drive[T](work: Work[T]) -> T:
    """Run `work`, a coroutine or a factory of one, to its result from sync code.

    Thread-sensitive hops run on the calling thread, also while it waits in a hop.
    """
    if running_loop() is not None:
        if inspect.iscoroutine(work):
            work.close()
        raise RuntimeError(_ON_LOOP)
    check_asgiref()
    previous = current_executor()
    with request_executor() as executor:
        run = _Run(work, executor)
        run.outcome.add_done_callback(lambda _: executor.wake())
        loop = launch(run.start)
        try:
            executor.serve(run.outcome)
        except BaseException:
            loop.call_soon_threadsafe(run.cancel)
            executor.serve(run.outcome)
            raise
        finally:
            if run.returned is not None:
                _carry(run.returned)
            route_to(previous)
    return run.outcome.result()


__all__ = ["Work", "drive"]
