"""Blocking work run off the event loop on a thread pool, with fresh DB connections."""

import asyncio
import inspect
import logging
import os
import threading
from collections.abc import Awaitable, Callable, Iterator, MutableMapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from contextvars import ContextVar, Token
from types import TracebackType
from typing import Self, override
from weakref import WeakKeyDictionary

from asgiref.sync import sync_to_async
from django.db import close_old_connections
from django.dispatch import Signal

from next.utils import after_fork

from .errors import PoolFullError


_dispatch_logger = logging.getLogger("django.dispatch")
_ROBUST_FAILURE = "Error calling %s in Signal.send_robust() (%s)"
"""The message `Signal._log_robust_failure` logs, which django-stubs leave out."""

type _Responses = list[tuple[Callable[..., object], object]]


async def run_detached[**P, R](
    func: Callable[P, R], *args: P.args, **kwargs: P.kwargs
) -> R:
    """Run blocking subscriber code on the shared pool instead of the one sync thread.

    A long-lived socket opens no thread-sensitive context, so such calls serialize.
    """
    call = _fresh_links(func, *args, **kwargs)
    return await sync_to_async(call, thread_sensitive=False)()


async def run_pooled[**P, R](
    executor: ThreadPoolExecutor,
    func: Callable[P, R],
    *args: P.args,
    **kwargs: P.kwargs,
) -> R:
    """Run blocking code on its own pool, so it never queues the shared one."""
    call = _fresh_links(func, *args, **kwargs)
    return await sync_to_async(call, thread_sensitive=False, executor=executor)()


class ThreadShare:
    """The handler pool threads one connection holds at most and holds now."""

    __slots__ = ("busy", "limit")

    def __init__(self, limit: int) -> None:
        """Start with no thread busy and `limit` threads to take."""
        self.limit = limit
        self.busy = 0


_LEASE: ContextVar["Lease | None"] = ContextVar("next_realtime_lease", default=None)


class Lease:
    """One event's hold on its connection slot, kept until its pool threads return.

    `release` runs once on the event loop, after the `with` block and the last thread.
    """

    __slots__ = ("_closed", "_release", "_share", "_threads", "_token")
    _token: Token["Lease | None"]

    def __init__(self, share: ThreadShare, release: Callable[[], None]) -> None:
        """Bind the lease to its connection's share and the slot it frees."""
        self._share = share
        self._release: Callable[[], None] | None = release
        self._threads = 0
        self._closed = False

    def __enter__(self) -> Self:
        """Make the lease the one sync handlers of this task claim threads under."""
        self._token = _LEASE.set(self)
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """End the event, freeing the slot now unless a thread still runs."""
        _LEASE.reset(self._token)
        self._closed = True
        self._settle()

    def take(self) -> bool:
        """Count one more thread for the event, False once the share is used up."""
        if self._share.busy >= self._share.limit:
            return False
        self._share.busy += 1
        self._threads += 1
        return True

    def give(self) -> None:
        """Count a thread of the event as returned."""
        self._share.busy -= 1
        self._threads -= 1
        self._settle()

    def _settle(self) -> None:
        release = self._release
        if self._closed and not self._threads and release is not None:
            self._release = None
            release()


class PoolThread:
    """A claimed handler pool thread, given back when the handler on it returns."""

    __slots__ = ("_executor", "_lease", "_permits", "_running")

    def __init__(
        self,
        executor: ThreadPoolExecutor,
        permits: threading.BoundedSemaphore,
        lease: Lease | None,
        running: set[asyncio.Task[object]],
    ) -> None:
        """Hold a taken permit, the lease it counts against and the pool to run on."""
        self._executor = executor
        self._permits = permits
        self._lease = lease
        self._running = running

    def give(self) -> None:
        """Return the thread to the pool and to the lease."""
        self._permits.release()
        if self._lease is not None:
            self._lease.give()

    async def run[T](self, handler: Callable[[T], object], event: T) -> None:
        """Run a sync handler on the thread, a cancel leaving it to finish alone.

        A sync-looking handler that returns an awaitable is awaited, so `ok` means done.
        """
        task = asyncio.create_task(run_pooled(self._executor, handler, event))
        self._running.add(task)
        task.add_done_callback(self._returned)
        outcome = await asyncio.shield(task)
        if inspect.isawaitable(outcome):
            await outcome

    def _returned(self, task: asyncio.Task[object]) -> None:
        """Give the thread back once the handler returned and retrieve its outcome."""
        self._running.discard(task)
        self.give()
        with suppress(asyncio.CancelledError):
            task.exception()


class HandlerPool:
    """The bulkhead sync upstream handlers run in, apart from the pool Django shares.

    A slow handler fills only these threads, and one connection only its share of them.
    """

    __slots__ = ("_running", "executor", "permits", "workers")
    workers: int
    executor: ThreadPoolExecutor
    permits: threading.BoundedSemaphore

    def __init__(self) -> None:
        """Size the pool like Django's default executor."""
        self._running: set[asyncio.Task[object]] = set()
        self.reset()

    def reset(self) -> None:
        """Start a fresh executor and fresh permits, as a forked child has to."""
        self.workers = min(32, (os.cpu_count() or 1) + 4)
        self.executor = ThreadPoolExecutor(
            self.workers, thread_name_prefix="next-handler"
        )
        self.permits = threading.BoundedSemaphore(self.workers)

    def share(self) -> ThreadShare:
        """Return a connection's share of the threads, half the pool."""
        return ThreadShare(max(1, self.workers // 2))

    def claim(self) -> PoolThread:
        """Take a free thread under the current lease, else `PoolFullError`.

        A permit stays taken until its handler returns, past a timeout too.
        """
        permits = self.permits
        if not permits.acquire(blocking=False):
            raise PoolFullError
        lease = _LEASE.get()
        if lease is not None and not lease.take():
            permits.release()
            raise PoolFullError
        return PoolThread(self.executor, permits, lease, self._running)


handler_pool = HandlerPool()
after_fork(handler_pool.reset)


def _fresh_links[**P, R](
    func: Callable[P, R], *args: P.args, **kwargs: P.kwargs
) -> Callable[[], R]:
    """Wrap a call in `close_old_connections`, as a request cycle would."""

    def call() -> R:
        close_old_connections()
        try:
            return func(*args, **kwargs)
        finally:
            close_old_connections()

    return call


class _SenderCache(MutableMapping[object, object]):
    """A receiver cache keyed weakly by sender, which skips a sender weakref misses.

    Django's `WeakKeyDictionary` raises `TypeError` there, the miss looks up anew.
    """

    __slots__ = ("_held",)

    def __init__(self) -> None:
        self._held: WeakKeyDictionary[object, object] = WeakKeyDictionary()

    @override
    def __getitem__(self, sender: object) -> object:
        try:
            return self._held[sender]
        except TypeError as exc:
            raise KeyError(sender) from exc

    @override
    def __setitem__(self, sender: object, receivers: object) -> None:
        try:
            self._held[sender] = receivers
        except TypeError:
            return

    @override
    def __delitem__(self, sender: object) -> None:
        try:
            del self._held[sender]
        except TypeError as exc:
            raise KeyError(sender) from exc

    @override
    def __iter__(self) -> Iterator[object]:
        return iter(self._held)

    @override
    def __len__(self) -> int:
        return len(self._held)


class PooledSignal(Signal):
    """A signal whose async send runs sync receivers on the shared pool, cleaned up.

    `Signal.asend_robust` queues every socket's sync receivers on one shared thread.
    """

    def __init__(self, *, use_caching: bool = False) -> None:
        """Build the signal, its receiver cache taking a sender with no weakref."""
        super().__init__(use_caching=use_caching)
        if use_caching:
            self.sender_receivers_cache = _SenderCache()

    @override
    async def asend_robust(self, sender: object, **named: object) -> _Responses:
        """Run sync receivers off the loop, then await the async ones together."""
        blocking, waiting = self._live_receivers(sender)
        if not (blocking or waiting):
            return []
        responses = (
            await run_detached(self._robust, blocking, sender, named)
            if blocking
            else []
        )
        answers = await asyncio.gather(
            *(self._awaited(receiver, sender, named) for receiver in waiting)
        )
        responses.extend(zip(waiting, answers, strict=True))
        return responses

    def _robust(
        self,
        receivers: list[Callable[..., object]],
        sender: object,
        named: dict[str, object],
    ) -> _Responses:
        responses: _Responses = []
        for receiver in receivers:
            try:
                response = receiver(signal=self, sender=sender, **named)
            except Exception as error:
                reason = str(error)
                _dispatch_logger.exception(_ROBUST_FAILURE, _name(receiver), reason)
                response = error
            responses.append((receiver, response))
        return responses

    async def _awaited(
        self,
        receiver: Callable[..., Awaitable[object]],
        sender: object,
        named: dict[str, object],
    ) -> object:
        try:
            return await receiver(signal=self, sender=sender, **named)
        except Exception as error:
            reason = str(error)
            _dispatch_logger.exception(_ROBUST_FAILURE, _name(receiver), reason)
            return error


def _name(receiver: Callable[..., object]) -> object:
    return getattr(receiver, "__qualname__", receiver)


__all__ = ["HandlerPool", "PooledSignal", "handler_pool", "run_detached", "run_pooled"]
