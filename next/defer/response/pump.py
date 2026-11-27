"""The two pumps that read a stream's batches and hand its writer's bytes on.

The sync one runs on the request thread, the async one on the server loop, and both
leave every decision to the writer and the lifetime they drive.
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import TYPE_CHECKING

from asgiref.sync import sync_to_async

from next.conf.defer import defer_seconds
from next.defer.signals import page_stream_closed, page_stream_opened


if TYPE_CHECKING:
    from collections.abc import (
        AsyncGenerator,
        AsyncIterator,
        Awaitable,
        Callable,
        Iterator,
    )

    from next.defer.markers import Piece

    from .lifetime import Lifetime
    from .writer import Writer


def on_request_thread[T](work: Callable[[], T]) -> Awaitable[T]:
    """Return `work` run on the request thread, as an error UI may query a database."""
    return sync_to_async(work, thread_sensitive=True)()


def pump(writer: Writer, runs: Iterator[tuple[Piece, ...]]) -> Iterator[bytes]:
    """Yield the bytes of each write on the request thread, until the jobs run out."""
    while (writes := _next(writer, runs)) is not None:
        yield from writes


def _next(writer: Writer, runs: Iterator[tuple[Piece, ...]]) -> list[bytes] | None:
    with writer.life.guard():
        pieces = next(runs, None)
        if pieces is not None:
            return [writer.joined(write) for write in writer.feed(pieces)]
    return None


async def apump(
    writer: Writer, runs: AsyncIterator[tuple[Piece, ...]]
) -> AsyncIterator[bytes]:
    """Yield the bytes of each write on the server loop, until the jobs run out."""
    while (writes := await _anext(writer, runs)) is not None:
        for data in writes:
            yield data


async def _anext(
    writer: Writer, runs: AsyncIterator[tuple[Piece, ...]]
) -> list[bytes] | None:
    with writer.life.guard():
        pieces = await anext(runs, None)
        if pieces is not None:
            writes: list[bytes] = []
            for write in writer.feed(pieces):
                parts = [
                    part
                    if isinstance(part, bytes)
                    else await on_request_thread(partial(writer.rescue, part))
                    for part in write
                ]
                writes.append(b"".join(parts))
            return writes
    return None


async def watched(
    life: Lifetime, body: AsyncGenerator[bytes, None]
) -> AsyncGenerator[bytes, None]:
    """Yield the writes of `body`, closing it once a write waits past `WRITE_TIMEOUT`.

    The loop runs on while the server awaits a slow client, so a timer frees its slot.
    """
    loop = asyncio.get_running_loop()
    limit = defer_seconds("WRITE_TIMEOUT")
    closing: list[asyncio.Task[None]] = []

    def stall() -> None:
        closing.append(life.stalled(body, limit))

    try:
        async for data in body:
            timer = loop.call_later(limit, stall)
            try:
                yield data
            finally:
                timer.cancel()
            if closing:
                return
    finally:
        if closing:
            await closing[0]
        else:
            await body.aclose()


def start(life: Lifetime) -> None:
    """Start the body, announcing it once the view's transactions have settled."""
    announced = life.opening()
    if announced is not None:
        page_stream_opened.send_robust(sender=life.sender, **announced)


async def astart(life: Lifetime) -> None:
    """Start the body on the server loop."""
    life.loop = asyncio.get_running_loop()
    announced = life.opening()
    if announced is not None:
        await page_stream_opened.asend_robust(sender=life.sender, **announced)


def end(life: Lifetime, *, aborted: bool) -> None:
    """End the stream once, announcing how it closed."""
    announced = life.closing(aborted=aborted)
    if announced is not None:
        page_stream_closed.send_robust(sender=life.sender, **announced)


async def aend(life: Lifetime, *, aborted: bool) -> None:
    """End the stream once from the server loop."""
    announced = life.closing(aborted=aborted)
    if announced is not None:
        await page_stream_closed.asend_robust(sender=life.sender, **announced)


__all__ = [
    "aend",
    "apump",
    "astart",
    "end",
    "on_request_thread",
    "pump",
    "start",
    "watched",
]
