"""The page stream, its frames and the response that writes them in order."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Iterable
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, Protocol, cast, override

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.handlers.asgi import ASGIRequest
from django.db import connections
from django.http import StreamingHttpResponse
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.utils.html import escape

from next.compression import prepare_stream
from next.defer import Addressing, DebugBoundaryFailure, Delivery, ErrorKind
from next.diagnostics import FailureLog
from next.partial import keys
from next.partial.envelope import Patch
from next.partial.patches import Patches
from next.partial.render import ZoneRenderResult
from next.static.collector import StaticCollector, default_placeholders
from next.static.runtime import BUNDLE_NOTES
from next.suspense.markers import Outcome
from next.suspense.render import Added, Delta, Merged
from next.suspense.session import seal_of
from next.suspense.signals import page_stream_closed, page_stream_opened

from .markup import closed, strip, wrappers


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Generator, Iterator
    from pathlib import Path

    from django.http import HttpRequest

    from next.compression import StreamEncoding
    from next.partial.envelope import Envelope
    from next.static.assets import StaticAsset
    from next.static.runtime import NextScriptBuilder
    from next.suspense.coordinator import Totals
    from next.suspense.markers import Job
    from next.suspense.policy import StreamSlot


logger = logging.getLogger("next.suspense")

failure_log = FailureLog(logger)

type Build = Callable[[Piece], bytes]
"""What serialises one settled piece as the frame its stream writes."""


_PAD_TO: Final = 1024
_PAD_FRAME: Final = len("<!---->")
_NOINDEX: Final = "noindex"
_META: Final = "meta"
_ROBOTS: Final = "robots"
_INDEXED_KINDS: Final = frozenset({ErrorKind.NOT_FOUND, ErrorKind.FORBIDDEN})
_ROOT: Final = re.compile(r"<(?:html|body)(?=[\s>])", re.IGNORECASE)
_HEAD_CLOSE: Final = "</head>"
_NOSCRIPT: Final = (
    "<noscript><style{nonce}>[data-next-state=loading]{{display:none}}</style>"
    "</noscript>"
)
_STREAM_ATTR: Final = " data-next-streamed"
_FRESHNESS: Final = frozenset(
    {
        "public",
        "private",
        "no-cache",
        "max-age",
        "s-maxage",
        "stale-while-revalidate",
        "stale-if-error",
        "immutable",
    }
)
_DROPPED: Final = ("ETag", "Last-Modified", "Content-Length")
_VARY: Final = ("Accept-Encoding", "Accept")
_FAILED: Final = "The stream of a page failed, so it ends with what it already wrote."
_FRAME: Final = "A frame of a stream failed to build, so it shows its error UI instead."
_NOTED: Final = (
    "A streamed deferred job noted %r for the script injection, which ran with the "
    "shell, so the page never reads it. Render the component inline there, "
    "defer='inline' at the call site."
)


@dataclass(frozen=True, slots=True)
class Piece:
    """One settled job a page stream writes, `parent` the stable key of its parent."""

    job: Job[Delta]
    outcome: Outcome[Delta]
    parent: str | None = None


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
    def totals(self) -> Totals | None:
        """Return the counts of the finished run, `None` before it finished."""

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
    """The stream slot a view reserved and the facts it reports of the stream it opens.

    The stream owns the slot from then on, so the view never gives it back itself.
    """

    slot: StreamSlot | None = None
    page_path: Path | None = None
    delivery: Delivery = Delivery.STREAM
    reason: str = "stream"
    boundaries: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Document:
    """The shell of a streamed page cut where its chunks go."""

    head: str
    tail: str


def dress(html: str, split: int, *, nonce: str | None, noscript: bool) -> Document:
    """Return the shell cut at `split`, marked as streamed and hiding loading UI.

    The root element carries `data-next-streamed`, and a page read without scripts
    hides each loading wrapper behind a `<noscript>` style rather than keep it for good.
    """
    head, tail = html[:split], html[split:]
    root = _ROOT.search(head)
    if root is not None:
        at = root.end()
        head = f"{head[:at]}{_STREAM_ATTR}{head[at:]}"
    if noscript:
        style = _NOSCRIPT.format(nonce=_nonce(nonce))
        at = head.find(_HEAD_CLOSE)
        head = head + style if at < 0 else f"{head[:at]}{style}{head[at:]}"
    return Document(head, tail)


def _directive(part: str) -> str:
    """Return the lower-cased name of one `Cache-Control` directive."""
    return part.split("=", 1)[0].strip().lower()


def _nonce(nonce: str | None) -> str:
    return "" if nonce is None else f' nonce="{escape(nonce)}"'


def _sent_key(asset: StaticAsset) -> tuple[str, str, str | None]:
    return asset.kind, asset.url, asset.inline


def _inner(tag: str, element: str) -> str:
    """Return the content of `element`, a wrapper of `tag` with escaped attributes."""
    return element[element.index(">") + 1 : len(element) - len(f"</{tag}>")]


class Payloads:
    """Turn the settled jobs of one stream into the wire mappings it writes.

    The ledger holds every asset the shell or an earlier write carried, so none repeats.
    """

    def __init__(
        self,
        request: HttpRequest,
        *,
        shell: dict[str, tuple[str | None, str]],
        collector: StaticCollector | None,
    ) -> None:
        """Hold the wrappers of the shell and the assets it already carries."""
        self._request = request
        self._wrappers = dict(shell)
        self._debug = bool(settings.DEBUG)
        self._sent: set[tuple[str, str, str | None]] = set()
        if collector is not None:
            for slot in default_placeholders:
                self._sent.update(map(_sent_key, collector.assets_in_slot(slot.name)))

    def payload(self, piece: Piece) -> dict[str, object]:
        """Return the wire mapping of one chunk, its envelope built as any partial's."""
        outcome = piece.outcome
        rendered = outcome.rendered
        ident = outcome.id
        data: dict[str, object] = {
            keys.CHUNK_BOUNDARY: ident,
            keys.CHUNK_STATE: rendered.state.value,
        }
        if rendered.kind is not None:
            data[keys.CHUNK_KIND] = rendered.kind.value
            if isinstance(rendered.failure, DebugBoundaryFailure):
                data[keys.CHUNK_DIGEST] = rendered.failure.digest
        if piece.parent is not None:
            data[keys.CHUNK_PARENT] = piece.parent
        data[keys.CHUNK_ENVELOPE] = self._envelope(piece).as_dict()
        if self._debug:
            timing = outcome.timing
            placement = timing.placement
            data[keys.CHUNK_DEBUG] = {
                "lane": None if timing.lane is None else timing.lane.value,
                "placement": None if placement is None else placement.reason.value,
                "queue_ms": round(timing.waited * 1000, 3),
                "run_ms": round(timing.ran * 1000, 3),
            }
        return data

    def _envelope(self, piece: Piece) -> Envelope:
        rendered = piece.outcome.rendered
        ident = piece.outcome.id
        seal = seal_of(ident)
        self._wrappers.update(wrappers(rendered.html, seal))
        body = strip(rendered.html, seal)
        tag, attributes = self._wrappers.get(ident, ("div", ""))
        element = closed(
            tag, attributes, body, rendered.kind, rendered.failure, rendered.retry
        )
        patches = Patches(self._request)
        target = {keys.BOUNDARY: ident}
        if tag is None:
            patches.replace(target, element)
        else:
            patches.inner(target, _inner(tag, element))
        patches.absorb_zone_result(
            ZoneRenderResult(html={}, bodies={}, collector=self._fresh(rendered.delta))
        )
        envelope = patches.envelope()
        if (
            piece.job.site.addressing is Addressing.PAGE
            and rendered.kind in _INDEXED_KINDS
        ):
            meta = Patch(op=_META, extras={_ROBOTS: _NOINDEX})
            envelope = replace(envelope, ops=(*envelope.ops, meta))
        self._wrappers.pop(ident, None)
        return envelope

    def _fresh(self, delta: Delta) -> StaticCollector:
        """Return a collector of what `delta` adds that no earlier write carried."""
        collector = StaticCollector()
        for step in delta.steps:
            if isinstance(step, Added):
                key = _sent_key(step.asset)
                if key not in self._sent:
                    self._sent.add(key)
                    collector.add(step.asset, prepend=step.prepend)
            elif isinstance(step, Merged):
                collector.add_js_context(
                    step.key, step.value, serializer=step.serializer
                )
            elif step.key not in BUNDLE_NOTES:
                failure_log.warn(("noted", step.key), _NOTED, step.key)
        return collector


class Chunks(Payloads):
    """Turn the settled jobs of one page stream into the chunk scripts it writes."""

    def __init__(
        self,
        request: HttpRequest,
        *,
        shell: dict[str, tuple[str | None, str]],
        collector: StaticCollector | None,
        builder: NextScriptBuilder,
        nonce: str | None,
    ) -> None:
        """Hold the payloads of the stream and the script builder of its page."""
        super().__init__(request, shell=shell, collector=collector)
        self._builder = builder
        self._nonce = nonce

    def end(self) -> str:
        """Return the script marking that no chunk follows."""
        return self._builder.chunk_script(json.dumps(keys.CHUNK_END), nonce=self._nonce)

    def script(self, piece: Piece) -> str:
        """Return the chunk script of one piece."""
        payload = json.dumps(self.payload(piece), separators=(",", ":"))
        return self._builder.chunk_script(payload, nonce=self._nonce)


def apart(pieces: Iterable[Piece]) -> list[tuple[Piece, ...]]:
    """Return a batch cut into writes so no chunk shares one with its parent's.

    A target the parent's chunk inserts is missing until that chunk applies.
    """
    writes: list[tuple[Piece, ...]] = []
    held: list[Piece] = []
    ids: set[str] = set()
    for piece in pieces:
        if piece.job.parent in ids:
            writes.append(tuple(held))
            held, ids = [], set()
        held.append(piece)
        ids.add(piece.outcome.id)
    if held:
        writes.append(tuple(held))
    return writes


def errored(piece: Piece) -> Piece:
    """Return `piece` settled as an `error`, the error UI of its job in its place."""
    job = piece.job
    return Piece(job, Outcome(job.id, job.fallback(ErrorKind.ERROR)), piece.parent)


def release_connections() -> None:
    """Close the connections of this thread no atomic block holds, as Django does."""
    for connection in connections.all(initialized_only=True):
        if not connection.in_atomic_block:
            connection.close_if_unusable_or_obsolete()


class Lifetime:
    """The slot, the counts and the close of one stream, page or partial.

    It owns the slot its view reserved and gives it back once, as the body ends, as the
    response closes, or as the response is collected unread.
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
        """Hold the jobs and take over the slot of `grant`, nothing run yet."""
        self.request = request
        self.batches = batches
        self.grant = grant
        self.slot = grant.slot
        if self.slot is not None:
            self.slot.bind(self)
        self.page_path = grant.page_path
        self.sender = sender
        self.partial = partial
        self.opened = time.monotonic()
        self.lock = threading.Lock()
        self.started = False
        self.ended = False
        self.logged = False
        self.sent = 0
        self.failed = 0
        self.loop: asyncio.AbstractEventLoop | None = None
        self.tasks: set[asyncio.Task[None]] = set()
        self.asynchronous = isinstance(request, ASGIRequest)

    def released(self) -> None:
        """Close the connections the jobs left, before the end."""
        with self.guard():
            release_connections()

    async def areleased(self) -> None:
        """Close the connections the jobs left, on the request thread."""
        with self.guard():
            await sync_to_async(release_connections, thread_sensitive=True)()

    @contextmanager
    def guard(self, message: str = _FAILED) -> Iterator[None]:
        """Contain a failure of the machinery, logging only the first of a stream."""
        try:
            yield
        except Exception:
            if not self.logged:
                self.logged = True
                logger.exception(message)

    def contained[T](self, work: Callable[[], T], message: str = _FAILED) -> T | None:
        """Return what `work` returns, `None` once it failed, as `guard` contains it."""
        try:
            return work()
        except Exception:
            if not self.logged:
                self.logged = True
                logger.exception(message)
        return None

    @property
    def broken(self) -> bool:
        """Whether the stream or the machinery running its jobs failed."""
        return self.logged or self.batches.failed

    def count(self, pieces: tuple[Piece, ...]) -> None:
        """Count the pieces a write carries and those that settled as an error."""
        self.sent += len(pieces)
        self.failed += sum(piece.outcome.kind is not None for piece in pieces)

    def framed(self, piece: Piece, build: Build) -> bytes | None:
        """Return the frame of one piece, `None` once building it failed."""
        return self.contained(lambda: build(piece), _FRAME)

    def fallback(self, piece: Piece, build: Build) -> bytes:
        """Return the frame of one piece settled as an `error` in its place."""
        self.failed += piece.outcome.kind is None
        return build(errored(piece))

    def frames(self, pieces: Iterable[Piece], build: Build) -> bytes:
        """Return the frames of `pieces`, a piece whose frame fails as its error UI."""
        data: list[bytes] = []
        for piece in pieces:
            framed = self.framed(piece, build)
            data.append(self.fallback(piece, build) if framed is None else framed)
        return b"".join(data)

    async def aframes(self, pieces: Iterable[Piece], build: Build) -> bytes:
        """Return what `frames` does, an error UI rendered on the request thread."""
        data: list[bytes] = []
        for piece in pieces:
            framed = self.framed(piece, build)
            if framed is None:
                fallback = sync_to_async(self.fallback, thread_sensitive=True)
                framed = await fallback(piece, build)
            data.append(framed)
        return b"".join(data)

    def owed(self, build: Build) -> bytes:
        """Return an `error` frame for each boundary a failure left without one."""
        return self.contained(lambda: self._owed(build)) or b""

    def _owed(self, build: Build) -> bytes:
        pieces = self.batches.unsettled()
        self.count(pieces)
        return self.frames(pieces, build)

    async def aowed(self, build: Build) -> bytes:
        """Return what `owed` does, from the request thread."""
        return await sync_to_async(self.owed, thread_sensitive=True)(build)

    def begin(self) -> bool:
        """Mark the body started, false when the stream already ended."""
        with self.lock:
            self.started = not self.ended
        return self.started

    def start(self) -> None:
        """Start the body, announcing it once the view's transactions have settled."""
        if self.begin() and page_stream_opened.has_listeners(self.sender):
            page_stream_opened.send_robust(sender=self.sender, **self.announced())

    async def astart(self) -> None:
        """Start the body on the server loop."""
        self.loop = asyncio.get_running_loop()
        if self.begin() and page_stream_opened.has_listeners(self.sender):
            await page_stream_opened.asend_robust(
                sender=self.sender, **self.announced()
            )

    def announced(self) -> dict[str, object]:
        """Return what `page_stream_opened` reports, the lane as the jobs see it."""
        grant = self.grant
        return {
            "page_path": self.page_path,
            "delivery": grant.delivery,
            "reason": grant.reason,
            "boundaries": grant.boundaries,
            "request_lane": self.batches.pinning_rule(),
            "partial": self.partial,
            "request": self.request,
        }

    def take_end(self) -> bool:
        """Mark the stream ended and give its slot back, true when its close is told.

        A stream whose body never started announced no opening, so it announces no end.
        """
        with self.lock:
            if self.ended:
                return False
            self.ended = True
            slot, self.slot = self.slot, None
        if slot is not None:
            slot.release()
        return self.started

    def end(self, *, aborted: bool) -> None:
        """End the stream once, announcing how it closed."""
        if self.take_end() and page_stream_closed.has_listeners(self.sender):
            page_stream_closed.send_robust(
                sender=self.sender, **self.closed(aborted=aborted)
            )

    async def aend(self, *, aborted: bool) -> None:
        """End the stream once from the server loop."""
        if self.take_end() and page_stream_closed.has_listeners(self.sender):
            await page_stream_closed.asend_robust(
                sender=self.sender, **self.closed(aborted=aborted)
            )

    def closed(self, *, aborted: bool) -> dict[str, object]:
        """Return what `page_stream_closed` reports of the stream."""
        totals = self.batches.totals
        return {
            "page_path": self.page_path,
            "sent": self.sent,
            "failed": self.failed,
            "late": 0 if totals is None else totals.late,
            "shed": 0 if totals is None else totals.shed,
            "duration_ms": (time.monotonic() - self.opened) * 1000,
            "aborted": aborted,
            "partial": self.partial,
            "request": self.request,
        }

    def close_later(self, body: AsyncGenerator[bytes, None]) -> None:
        """Ask the loop to close a body its reader left suspended, without waiting."""
        loop = self.loop
        if loop is None or inspect.getasyncgenstate(body) != inspect.AGEN_SUSPENDED:
            return
        loop.call_soon_threadsafe(self.spawn, body)

    def spawn(self, body: AsyncGenerator[bytes, None]) -> None:
        """Close `body` in a task the loop keeps until it finishes."""
        task = asyncio.ensure_future(_aclosed(body))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)


class _Writer(Lifetime):
    """The body of one page stream, kept apart so the body never holds its response."""

    def __init__(
        self,
        request: HttpRequest,
        document: Document,
        batches: Batches,
        chunks: Chunks,
        grant: StreamGrant,
    ) -> None:
        """Hold the shell encoded, so text no byte spells fails before the headers."""
        head, tail = document.head.encode(), document.tail.encode()
        super().__init__(request, batches, grant, sender=StreamingPageResponse)
        self.head: bytes | None = head
        self.tail = tail
        self.chunks = chunks
        self.encoding: StreamEncoding | None = None

    def body(self) -> Iterator[bytes]:
        """Yield the shell, a write per batch, then the end and the tail."""
        try:
            self.start()
            yield self.encode(self.first())
            runs = self.batches.run()
            try:
                while (writes := self.next(runs)) is not None:
                    for data in writes:
                        yield self.encode(data)
            finally:
                runs.close()
            self.released()
            owed = self.owed(self.chunk) if self.broken else b""
            yield self.encode(owed + self.last()) + self.coded_end()
            self.end(aborted=False)
        finally:
            self.end(aborted=True)

    async def abody(self) -> AsyncIterator[bytes]:
        """Yield what `body` does on the running server loop."""
        try:
            await self.astart()
            yield await self.aencode(self.first(), offload=True)
            runs = self.batches.arun()
            try:
                while (writes := await self.anext(runs)) is not None:
                    for data in writes:
                        yield await self.aencode(data)
            finally:
                await runs.aclose()
            await self.areleased()
            owed = await self.aowed(self.chunk) if self.broken else b""
            yield await self.aencode(owed + self.last()) + self.coded_end()
            await self.aend(aborted=False)
        finally:
            await self.aend(aborted=True)

    def next(self, runs: Iterator[tuple[Piece, ...]]) -> list[bytes] | None:
        """Return the writes of the next batch, `None` once the jobs ran out."""
        writes = None
        with self.guard():
            pieces = next(runs, None)
            if pieces is not None:
                writes = self.writes(pieces)
        return writes

    async def anext(self, runs: AsyncIterator[tuple[Piece, ...]]) -> list[bytes] | None:
        """Return the writes of the next batch on the server loop."""
        writes = None
        with self.guard():
            pieces = await anext(runs, None)
            if pieces is not None:
                self.count(pieces)
                writes = [
                    await self.aframes(part, self.chunk) for part in apart(pieces)
                ]
        return writes

    def first(self) -> bytes:
        """Return the shell up to the split, padded for a browser that buffers less."""
        data = self.head or b""
        self.head = None
        if len(data) < _PAD_TO:
            data += b"<!--" + b" " * max(0, _PAD_TO - len(data) - _PAD_FRAME) + b"-->"
        return data

    def writes(self, pieces: tuple[Piece, ...]) -> list[bytes]:
        """Return the writes of one batch, a nested chunk apart from its parent's."""
        self.count(pieces)
        return [self.frames(part, self.chunk) for part in apart(pieces)]

    def chunk(self, piece: Piece) -> bytes:
        """Return the chunk script of one piece."""
        return self.chunks.script(piece).encode()

    def last(self) -> bytes:
        """Return the end marker and the rest of the page."""
        end = ""
        with self.guard():
            end = self.chunks.end()
        data = end.encode() + self.tail
        self.tail = b""
        return data

    def encode(self, data: bytes) -> bytes:
        """Return `data` padded, compressed and flushed when the stream has a coding."""
        encoding = self.encoding
        return data if encoding is None else encoding.write(data)

    async def aencode(self, data: bytes, *, offload: bool | None = None) -> bytes:
        """Return `encode(data)`, a write past `OFFLOAD_BYTES` compressed off the loop.

        `offload` forces the choice, as the shell always compresses in a worker thread.
        """
        encoding = self.encoding
        if encoding is None:
            return data
        return await encoding.awrite(data, offload=offload)

    def coded_end(self) -> bytes:
        """Return the bytes that end the coded stream, none for a plain one."""
        encoding = self.encoding
        return b"" if encoding is None else encoding.end()


class StreamingPageResponse(StreamingHttpResponse):
    """A page sent as its shell, a chunk per settled boundary, then its closing tags.

    The body is produced while it is read, sync under WSGI and async under ASGI, so a
    server never buffers it. It owns its stream slot until it ends, closes or drops.
    The status an error UI of a job sets never reaches it, as it already sent 200.
    """

    def __init__(
        self,
        request: HttpRequest,
        document: Document,
        batches: Batches,
        chunks: Chunks,
        *,
        grant: StreamGrant | None = None,
    ) -> None:
        """Hold the shell, the jobs and the writer, writing nothing yet."""
        writer = _Writer(request, document, batches, chunks, grant or StreamGrant())
        self._request = request
        self._writer = writer
        self._closing = False
        body: Iterator[bytes] | AsyncIterator[bytes] = (
            writer.abody() if writer.asynchronous else writer.body()
        )
        self._content = body
        super().__init__(body)

    def settle(self) -> None:
        """Make the stream private and revalidated, then negotiate its coding.

        A stricter directive the page set, such as `no-store` or `no-transform`, stays,
        and the coding is negotiated once the final `Cache-Control` is known.
        """
        kept = [
            part.strip()
            for part in self.get("Cache-Control", "").split(",")
            if part.strip() and _directive(part) not in _FRESHNESS
        ]
        self["Cache-Control"] = ", ".join(kept)
        patch_cache_control(self, private=True, no_cache=True)
        for name in _DROPPED:
            if self.has_header(name):
                del self[name]
        self["X-Accel-Buffering"] = "no"
        patch_vary_headers(self, _VARY)
        self._writer.encoding = prepare_stream(self._request, self)

    @override
    def close(self) -> None:
        """Close once, ending a stream its reader left as aborted."""
        if self._closing:
            return
        self._closing = True
        writer = self._writer
        try:
            if writer.asynchronous:
                writer.close_later(cast("AsyncGenerator[bytes, None]", self._content))
            super().close()
        finally:
            writer.end(aborted=True)

    def discard(self) -> None:
        """End a stream its view never returned, with no `request_finished` fired."""
        self._closing = True
        self._writer.end(aborted=True)


async def _aclosed(body: AsyncGenerator[bytes, None]) -> None:
    with suppress(RuntimeError):
        await body.aclose()


__all__ = [
    "Batches",
    "Build",
    "Chunks",
    "Document",
    "Lifetime",
    "Payloads",
    "Piece",
    "StreamGrant",
    "StreamingPageResponse",
    "apart",
    "dress",
    "errored",
    "failure_log",
    "release_connections",
]
