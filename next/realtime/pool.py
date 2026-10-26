"""Blocking work run off the event loop on a thread pool, with fresh DB connections."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import override

from asgiref.sync import sync_to_async
from django.db import close_old_connections
from django.dispatch import Signal


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


class PooledSignal(Signal):
    """A signal whose async send runs sync receivers on the shared pool, cleaned up.

    `Signal.asend_robust` queues every socket's sync receivers on one shared thread.
    """

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


__all__ = ["PooledSignal", "run_detached", "run_pooled"]
