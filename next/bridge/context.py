"""Snapshots of a request's context, restored on the threads and loops its work runs on.

asgiref 3.12 shows a `Local` value only on a thread that its own hop carried it to.
"""

import asyncio
import contextvars
import threading
from concurrent.futures import Future, InvalidStateError
from contextlib import suppress
from functools import partial
from typing import Any

from .deadline import remaining
from .hops import InlineExecutor, hop_on
from .loop import launch, listed, running_loop
from .request import RequestThreadExecutor, arrive_on, request_executor


_RESTORE_ON_LOOP = (
    "You cannot restore a context onto a thread running an event loop - await it there."
)
_LATE = "The deadline passed before the snapshot was restored on this thread."


def _idle() -> None:
    """Do nothing, as the hop that carries a task needs no work on its thread."""


_LAND = hop_on(_idle, InlineExecutor())


async def _landed() -> contextvars.Context:
    """Return the context of this task once a hop on the loop thread moved it here."""
    await _LAND()
    return contextvars.copy_context()


class ContextSnapshot:
    """A snapshot of the request's context, with a copy kept for each thread."""

    __slots__ = ("_copies", "_landings", "context", "thread")

    def __init__(self, context: contextvars.Context, thread: int | None = None) -> None:
        """Hold `context`, captured on `thread`, the calling one by default."""
        self.context = context
        self.thread = threading.get_ident() if thread is None else thread
        self._copies: dict[int, contextvars.Context] = {}
        self._landings: dict[int, asyncio.Task[contextvars.Context]] = {}

    def get(self) -> contextvars.Context | None:
        """Return the context restored on this thread, `None` until one was kept."""
        here = threading.get_ident()
        if here == self.thread:
            return self.context
        return self._copies.get(here)

    def keep(self, context: contextvars.Context) -> contextvars.Context:
        """Hold `context`, which a hop restored on this thread, and return it."""
        self._copies[threading.get_ident()] = context
        return context

    def restore(self) -> contextvars.Context:
        """Return the snapshot restored on this thread by a hop from a loop, kept after.

        It waits until the current deadline at most and refuses a thread running a loop.
        """
        found = self.get()
        if found is not None:
            return found
        if running_loop() is not None:
            raise RuntimeError(_RESTORE_ON_LOOP)
        arrived: Future[contextvars.Context] = Future()
        with request_executor() as executor:
            arrived.add_done_callback(lambda _: executor.wake())
            context = self.context.copy()
            launch(partial(_begin, executor, arrived, context))
            executor.serve(arrived, remaining())
        if not arrived.done():
            raise TimeoutError(_LATE)
        return self.keep(arrived.result())

    async def arestore(self) -> contextvars.Context:
        """Return the snapshot restored on the running loop by one hop for every caller.

        A cancelled caller leaves the hop running for the others.
        """
        found = self.get()
        if found is not None:
            return found
        here = threading.get_ident()
        landing = self._landings.get(here)
        if landing is None:
            loop = asyncio.get_running_loop()
            landing = loop.create_task(_landed(), context=self.context.copy())
            self._landings[here] = landing
        await asyncio.wait((landing,))
        return self.keep(landing.result())


async def _arrival(
    executor: RequestThreadExecutor, arrived: Future[contextvars.Context]
) -> None:
    """Carry this task onto the thread `executor` serves, a failure reaching it too."""
    try:
        await arrive_on(executor, arrived.set_result)
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
    listed(task, parents)


__all__ = ["ContextSnapshot"]
