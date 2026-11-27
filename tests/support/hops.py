import asyncio
from collections.abc import Callable
from concurrent.futures import Executor, Future

from next.bridge.hops import hop_on


class HereExecutor(Executor):
    """Run each call on the submitting thread, keeping the markers asgiref leaves."""

    def submit(self, fn: Callable[..., object], /, *args: object) -> Future[object]:
        future: Future[object] = Future()
        future.set_result(fn(*args))
        return future


def _noop() -> None:
    """Do nothing, as only the markers of the hop matter."""


def stale_marks(*, close: bool = True) -> asyncio.AbstractEventLoop:
    """Run one hop on this thread from a loop of its own and return the loop, ended.

    asgiref leaves the loop marked on the thread, as a finished server call would.
    """
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(hop_on(_noop, HereExecutor())())
    finally:
        if close:
            loop.close()
    return loop
