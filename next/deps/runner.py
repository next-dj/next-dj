"""The seam that runs a blocking segment of an async plan off the event loop."""

from __future__ import annotations

import logging
import threading
from contextvars import ContextVar
from dataclasses import replace
from functools import partial
from itertools import islice
from time import perf_counter
from typing import TYPE_CHECKING, Protocol

from asgiref.sync import sync_to_async
from django.conf import settings

from next.caches import BoundedCache
from next.ports import run_bridged

from .cache import _CACHE_MISS, DependencyCache, request_memo


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .context import ResolutionContext
    from .scope import RequestScope


logger = logging.getLogger(__name__)

_adapted: BoundedCache[str, None] = BoundedCache()
_adapted_lock = threading.Lock()


class SegmentRunner(Protocol):
    """Protocol of the call that runs one blocking segment and awaits its result."""

    def __call__[T](self, segment: Callable[[], T], /) -> Awaitable[T]:
        """Run `segment` where a blocking call is allowed and hand back its result."""


def run_on_request_thread[T](segment: Callable[[], T], /) -> Awaitable[T]:
    """Run `segment` on the thread Django gives the request, the default runner."""
    return sync_to_async(segment, thread_sensitive=True)()


segment_runner: ContextVar[SegmentRunner] = ContextVar(
    "segment_runner", default=run_on_request_thread
)
"""The runner every blocking segment goes through, swapped for a scope by setting it."""


batch_scope: ContextVar[RequestScope | None] = ContextVar(
    "next_deps_batch_scope", default=None
)
"""The request scope loaders batch in where no request memo is set, as in a segment."""


def run_segment[T](segment: Callable[[], T]) -> Awaitable[T]:
    """Hand `segment` to the segment runner current in this context."""
    return segment_runner.get()(segment)


def _unscoped[T](
    fill: Callable[[ResolutionContext], T], context: ResolutionContext
) -> T:
    """Call `fill` with no request memo, as only its loop thread writes a scope.

    Its loaders still batch in the scope, as a thread hands each key to its loop.
    """
    memo = request_memo.get()
    if memo is None:
        return fill(context)
    token = request_memo.set(None)
    batched = batch_scope.set(memo.scope)
    try:
        return fill(context)
    finally:
        batch_scope.reset(batched)
        request_memo.reset(token)


async def run_isolated[T](
    fill: Callable[[ResolutionContext], T], context: ResolutionContext
) -> T:
    """Run `fill` as one segment against a private copy of the context cache.

    Only keys added past the copy merge back, so no in-progress mark crosses.
    """
    private = DependencyCache(dict(context.cache.finished()))
    copied = len(private)
    branch = replace(context, cache=private, stack=list(context.stack))
    value = await run_segment(partial(_unscoped, fill, branch))
    cache = context.cache
    for key, found in islice(private.finished().items(), copied, None):
        if cache.get(key) is _CACHE_MISS:
            cache.set(key, found)
    return value


def bridge[T](site: object, work: Callable[[], Awaitable[T]]) -> T:
    """Run the async `work` a sync caller meets through the bridge port.

    DEBUG logs each site once by its `str`, built only there, with the cost of the hop.
    """
    if not settings.DEBUG:
        return run_bridged(work)
    label = str(site)
    if label in _adapted:
        return run_bridged(work)
    marks: list[float] = []

    async def measured() -> T:
        marks.append(perf_counter())
        value = await work()
        marks.append(perf_counter())
        return value

    started = perf_counter()
    value = run_bridged(measured)
    _note_adapted(label, perf_counter() - started - (marks[1] - marks[0]))
    return value


def _note_adapted(site: str, cost: float) -> None:
    """Log `site` once across threads, under a lock only the DEBUG path takes."""
    with _adapted_lock:
        if site in _adapted:
            return
        _adapted[site] = None
    logger.debug(
        "Asynchronous %s bridged for a sync caller at a cost of %.3f ms beyond its "
        "own work.",
        site,
        cost * 1000,
    )


__all__ = [
    "SegmentRunner",
    "batch_scope",
    "bridge",
    "run_isolated",
    "run_on_request_thread",
    "run_segment",
    "segment_runner",
]
