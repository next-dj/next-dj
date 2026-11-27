"""The executor the request thread serves while it waits for a bridged coroutine."""

import contextvars
import functools
import threading
import time
from collections.abc import Callable, Generator
from concurrent.futures import Future, InvalidStateError, wait
from contextlib import contextmanager, suppress
from queue import Empty, SimpleQueue
from typing import Any, override

from asgiref.sync import SyncToAsync

from .hops import QuitParentExecutor, route_to, unmarked
from .tasks import StealableTask


_UNSERVED = "The request thread no longer serves this coroutine."


class RequestThreadExecutor(QuitParentExecutor):
    """Queue thread-sensitive hops for the request thread, which runs them waiting."""

    def __init__(self) -> None:
        """Open an empty queue served by the thread that creates it."""
        self._queue: SimpleQueue[StealableTask[Any] | None] = SimpleQueue()
        self._closed = False
        self._thread = threading.current_thread()

    @property
    def closed(self) -> bool:
        """Whether the executor rejects hops, as its coroutine has finished."""
        return self._closed

    @property
    def thread(self) -> threading.Thread:
        """Return the thread that serves the hops."""
        return self._thread

    @override
    def submit[**P, R](
        self, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs
    ) -> Future[R]:
        """Queue the call without blocking, failing it once the executor is closed."""
        if threading.current_thread() is self._thread:
            msg = "You cannot submit onto RequestThreadExecutor from its own thread"
            raise RuntimeError(msg)
        hop = StealableTask(functools.partial(fn, *args, **kwargs))
        self._queue.put(hop)
        if self._closed:
            hop.reject(RuntimeError(_UNSERVED))
        return hop.future

    def wake(self) -> None:
        """Unblock the serving thread so it rechecks what it waits for."""
        self._queue.put(None)

    def serve(self, future: Future[Any], timeout: float | None = None) -> None:
        """Run queued hops on the calling thread until `future` is done or time is up.

        A zero `timeout` runs the hops already queued and returns without waiting.
        """
        until = None if timeout is None else time.monotonic() + timeout
        while not future.done():
            try:
                hop = self._next(until)
            except Empty:
                return
            if hop is None:
                continue
            if future.done():
                self._queue.put(hop)
                return
            unmarked(hop.run)

    def _next(self, until: float | None) -> StealableTask[Any] | None:
        """Take the next hop, raising `Empty` once the instant `until` has passed."""
        if until is None:
            return self._queue.get()
        return self._queue.get(timeout=max(0.0, until - time.monotonic()))

    def close(self) -> None:
        """Reject queued and later hops, since the coroutine has finished by now."""
        self._closed = True
        while True:
            try:
                hop = self._queue.get_nowait()
            except Empty:
                return
            if hop is not None:
                hop.reject(RuntimeError(_UNSERVED))


_serving = threading.local()


@contextmanager
def request_executor() -> Generator[RequestThreadExecutor]:
    """Yield the executor this thread serves, opened and closed by the outermost caller.

    A hop that drives again shares it, and a close on another thread spares this local.
    """
    current: RequestThreadExecutor | None = getattr(_serving, "executor", None)
    if current is not None and not current.closed:
        yield current
        return
    executor = RequestThreadExecutor()
    _serving.executor = executor
    try:
        yield executor
    finally:
        executor.close()
        if threading.current_thread() is executor.thread:
            del _serving.executor


def serve_until(future: Future[Any], /) -> None:
    """Block until `future` is done, running the hops queued for this thread meanwhile.

    A thread no driver serves, such as a pool thread, has no hops and waits plainly.
    """
    executor: RequestThreadExecutor | None = getattr(_serving, "executor", None)
    if executor is None or executor.closed:
        wait((future,))
        return
    future.add_done_callback(lambda _: executor.wake())
    executor.serve(future)


def _arrive(settle: Callable[[contextvars.Context], object]) -> None:
    """Hand the context the hop carried onto this thread to what waits on it."""
    with suppress(InvalidStateError):
        settle(contextvars.copy_context())


_ARRIVE = SyncToAsync(_arrive, thread_sensitive=True)


async def arrive_on(
    executor: RequestThreadExecutor, settle: Callable[[contextvars.Context], object]
) -> None:
    """Hop onto the thread `executor` serves, which hands `settle` the context there."""
    route_to(executor)
    await _ARRIVE(settle)


__all__ = ["RequestThreadExecutor", "arrive_on", "request_executor", "serve_until"]
