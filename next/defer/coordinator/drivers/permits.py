"""The process-wide permits that bound request-lane work under ASGI."""

import asyncio
import threading
import time
from collections import deque
from contextlib import suppress

from next.caches import FORK
from next.conf.defer import defer_count


type _Waiter = tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]


class RequestPermits:
    """The process-wide permits bounding request-lane work under ASGI.

    A waiter awaits a future of its own loop, so no loop ever waits on the lock.
    """

    def __init__(self, limit: int) -> None:
        """Hold `limit` free permits and no waiter."""
        self.limit = limit
        self._lock = threading.Lock()
        self._free = limit
        self._waiters: deque[_Waiter] = deque()

    def take(self) -> bool:
        """Take a free permit at once, false when none is free or one is owed."""
        with self._lock:
            if self._free and not self._waiters:
                self._free -= 1
                return True
        return False

    async def wait(self, until: float) -> bool:
        """Take a permit before the monotonic instant `until`, false once it passed.

        One hold of the lock checks and queues, so no release strands a free permit.
        """
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._free and not self._waiters:
                self._free -= 1
                return True
            waiter: asyncio.Future[None] = loop.create_future()
            entry = (loop, waiter)
            self._waiters.append(entry)
        try:
            async with asyncio.timeout(max(0.0, until - time.monotonic())):
                await waiter
        except TimeoutError:
            self._leave(entry)
            return False
        except asyncio.CancelledError:
            self._leave(entry)
            raise
        return True

    def release(self) -> None:
        """Hand a permit to the oldest waiter whose loop still runs, or free it."""
        with self._lock:
            while self._waiters:
                loop, waiter = self._waiters.popleft()
                try:
                    loop.call_soon_threadsafe(self._grant, waiter)
                except RuntimeError:
                    continue
                return
            self._free += 1

    def _grant(self, waiter: asyncio.Future[None]) -> None:
        if waiter.done():
            self.release()
        else:
            waiter.set_result(None)

    def _leave(self, entry: _Waiter) -> None:
        """Drop a waiter that stopped waiting, handing back a permit it was granted."""
        with self._lock, suppress(ValueError):
            self._waiters.remove(entry)
        waiter = entry[1]
        if waiter.done() and not waiter.cancelled():
            self.release()


_permits: dict[int, RequestPermits] = {}
_permits_lock = threading.Lock()
FORK.attach(_permits.clear)


def request_permits() -> RequestPermits:
    """Return the permits of this process, sized by `MAX_REQUEST_JOBS`."""
    limit = defer_count("EXECUTOR", "MAX_REQUEST_JOBS")
    held = _permits.get(limit)
    if held is None:
        with _permits_lock:
            held = _permits.setdefault(limit, RequestPermits(limit))
    return held


__all__ = ["RequestPermits", "request_permits"]
