"""The slot, the counts and the announcements of one stream, page or partial."""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from django.core.handlers.asgi import ASGIRequest
from django.db import connections

from next.boundaries.failures import ErrorKind
from next.defer.coordinator import Totals
from next.defer.markers import Delivery
from next.defer.signals import page_stream_closed, page_stream_opened

from .chunks import failure_log


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator
    from pathlib import Path

    from django.http import HttpRequest

    from next.defer.coordinator import Finish
    from next.defer.markers import Piece
    from next.defer.policy import StreamSlot


logger = logging.getLogger("next.defer")

_FAILED: Final = "The stream of a page failed, so it ends with what it already wrote."
_CUT: Final = (
    "The stream of %s on %s ran past NEXT_FRAMEWORK['DEFER']['DEADLINE'] plus "
    "WRITE_GRACE, so it was cut with %d boundaries unwritten, which the client marks "
    "incomplete and fetches again."
)
_STALLED: Final = (
    "The client of %s on %s took no write for %.1f seconds, so its stream was closed "
    "and its slot and jobs freed. NEXT_FRAMEWORK['DEFER']['WRITE_TIMEOUT'] sets the "
    "wait, and a proxy send_timeout bounds the connection itself."
)

type Announced = dict[str, object]
"""The keyword arguments a stream signal carries."""


class Batches(Protocol):
    """The settled jobs of one page stream, batch by batch, on either kind of server."""

    def run(
        self, *, idle: float | None = None
    ) -> Generator[tuple[Piece, ...], None, None]:
        """Yield the batches on the request thread, each ask acknowledging the last.

        With `idle` set, an empty batch follows every quiet `idle` seconds.
        """

    def arun(self) -> AsyncGenerator[tuple[Piece, ...], None]:
        """Yield the batches on the running server loop."""

    @property
    def finish(self) -> Finish | None:
        """Return how the run finished and its counts, `None` before it finished."""

    @property
    def failed(self) -> bool:
        """Whether the machinery of the run failed and stopped the jobs it held."""

    def unsettled(self) -> tuple[Piece, ...]:
        """Return an `error` piece for each boundary the run handed no piece for.

        It renders error UIs, so an async stream calls it on the request thread.
        """

    def pinning_rule(self) -> str | None:
        """Return the request-lane rule pinning a job, read in the state jobs run in."""


@dataclass(frozen=True, slots=True)
class StreamGrant:
    """The slot a view hands the stream it opens and the facts its signals report."""

    slot: StreamSlot | None = None
    page_path: Path | None = None
    delivery: Delivery = Delivery.STREAM
    reason: str = "stream"
    boundaries: tuple[tuple[str, str], ...] = ()


def release_connections() -> None:
    """Close the connections of this thread no atomic block holds, as Django does."""
    for connection in connections.all(initialized_only=True):
        if not connection.in_atomic_block:
            connection.close_if_unusable_or_obsolete()


class Lifetime:
    """The slot, the counts and the close of one stream, page or partial.

    It owns the slot its view reserved and gives it back once, as the body ends or as
    the response closes, the slot itself giving it back when the response is dropped.
    """

    def __init__(
        self,
        request: HttpRequest,
        batches: Batches,
        grant: StreamGrant,
        *,
        sender: type,
        partial: bool = False,
    ) -> None:
        """Hold the jobs and the slot, nothing run yet."""
        self.request = request
        self.batches = batches
        self.grant = grant
        self.slot = grant.slot
        self.sender = sender
        self.partial = partial
        self.opened = time.monotonic()
        self.lock = threading.Lock()
        self.started = False
        self.ended = False
        self.logged = False
        self.sent = 0
        self.failed = 0
        self.timed_out = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.tasks: set[asyncio.Task[None]] = set()
        self.asynchronous = isinstance(request, ASGIRequest)

    @contextmanager
    def guard(self, message: str = _FAILED) -> Generator[None]:
        """Contain a failure of the machinery, logging only the first of a stream."""
        try:
            yield
        except Exception:
            if not self.logged:
                self.logged = True
                logger.exception(message)

    @property
    def broken(self) -> bool:
        """Whether the stream or the machinery running its jobs failed."""
        return self.logged or self.batches.failed

    def count(self, pieces: tuple[Piece, ...]) -> None:
        """Count the pieces a write carries, those settled as an error or a timeout."""
        self.sent += len(pieces)
        for piece in pieces:
            kind = piece.kind
            if kind is not None:
                self.failed += 1
                self.timed_out = self.timed_out or kind is ErrorKind.TIMEOUT

    def opening(self) -> Announced | None:
        """Mark the body started, returning what `page_stream_opened` announces.

        It returns `None` once the stream ended or when no receiver listens.
        """
        with self.lock:
            self.started = not self.ended
        if not self.started or not page_stream_opened.has_listeners(self.sender):
            return None
        grant = self.grant
        return {
            "page_path": grant.page_path,
            "delivery": grant.delivery,
            "reason": grant.reason,
            "boundaries": grant.boundaries,
            "request_lane": self.batches.pinning_rule(),
            "partial": self.partial,
            "request": self.request,
        }

    def closing(self, *, aborted: bool) -> Announced | None:
        """End the stream once and give its slot back, returning what is announced.

        A stream whose body never started announced no opening, so it announces no end.
        """
        with self.lock:
            if self.ended:
                return None
            self.ended = True
            slot, self.slot = self.slot, None
        if slot is not None:
            slot.release()
        if not self.started:
            return None
        self._warn_cut()
        if not page_stream_closed.has_listeners(self.sender):
            return None
        finish = self.batches.finish
        totals = Totals() if finish is None else finish.totals
        return {
            "page_path": self.grant.page_path,
            "sent": self.sent,
            "failed": self.failed,
            "late": totals.late,
            "shed": totals.shed,
            "timeouts": totals.timeouts,
            "abandoned": totals.abandoned,
            "cancelled": totals.cancelled,
            "dropped": totals.dropped,
            "cut": finish is not None and finish.cut,
            "duration_ms": (time.monotonic() - self.opened) * 1000,
            "aborted": aborted,
            "partial": self.partial,
            "request": self.request,
        }

    def _warn_cut(self) -> None:
        """Warn of a stream its deadline cut, at a bounded rate per page."""
        finish = self.batches.finish
        if finish is None or not finish.cut:
            return
        page_path = self.grant.page_path
        page = "a partial response" if page_path is None else str(page_path)
        failure_log.emit(
            logging.WARNING,
            ("cut", page),
            _CUT,
            (page, self.request.path, finish.totals.dropped),
            extra={"request": self.request},
        )

    def stalled(
        self, body: AsyncGenerator[bytes, None], waited: float
    ) -> asyncio.Task[None]:
        """Warn of a client that took no write for `waited` seconds and close `body`.

        The close runs in a task the loop keeps, as the reader still awaits its write.
        """
        page_path = self.grant.page_path
        page = "a partial response" if page_path is None else str(page_path)
        failure_log.emit(
            logging.WARNING,
            ("stalled", page),
            _STALLED,
            (page, self.request.path, waited),
            extra={"request": self.request},
        )
        return self.spawn(body)

    def close_later(self, body: AsyncGenerator[bytes, None]) -> None:
        """Ask the loop to close a body its reader left suspended, without waiting."""
        loop = self.loop
        if loop is None or inspect.getasyncgenstate(body) != inspect.AGEN_SUSPENDED:
            return
        loop.call_soon_threadsafe(self.spawn, body)

    def spawn(self, body: AsyncGenerator[bytes, None]) -> asyncio.Task[None]:
        """Close `body` in a task the loop keeps until it finishes."""
        task = asyncio.ensure_future(_aclosed(body))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task


async def _aclosed(body: AsyncGenerator[bytes, None]) -> None:
    with suppress(RuntimeError):
        await body.aclose()


__all__ = ["Announced", "Batches", "Lifetime", "StreamGrant", "release_connections"]
