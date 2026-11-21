"""The boundary wrapper markup, its assembled inline delivery and the page stream."""

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
from typing import TYPE_CHECKING, Any, Final, Protocol, cast, override

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.handlers.asgi import ASGIRequest
from django.db import connections
from django.http import StreamingHttpResponse
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.utils.html import escape

from next.boundaries import adopt, failed, identity
from next.compression import prepare_stream
from next.defer import Addressing, DebugBoundaryError, DeferMode, Delivery, ErrorKind
from next.diagnostics import FailureLog, mark_degraded
from next.partial import keys
from next.partial.envelope import Patch
from next.partial.patches import Patches
from next.partial.render import ZoneRenderResult
from next.ports import run_bridged
from next.static.collector import StaticCollector, default_placeholders
from next.static.runtime import BUNDLE_NOTES
from next.suspense.markers import Outcome
from next.suspense.render import Added, Delta, Merged
from next.suspense.session import BoundarySession
from next.suspense.signals import page_stream_closed, page_stream_opened


if TYPE_CHECKING:
    from collections.abc import (
        AsyncGenerator,
        AsyncIterator,
        Coroutine,
        Generator,
        Iterator,
    )
    from pathlib import Path

    from django.http import HttpRequest

    from next.compression import StreamEncoding
    from next.defer import BoundaryError
    from next.partial.envelope import Envelope
    from next.static.assets import StaticAsset
    from next.static.runtime import NextScriptBuilder
    from next.suspense.coordinator import Totals
    from next.suspense.markers import Job, Rendered
    from next.suspense.policy import StreamSlot


logger = logging.getLogger("next.suspense")

_failures = FailureLog(logger)

_LOADING: Final = ' data-next-state="loading" aria-busy="true"'
_IDLE: Final = ' data-next-state="idle"'
_ADOPTED: Final = " data-next-adopted"
_STATE: Final = ' data-next-state="'
_OPENED: Final = re.compile(r"([0-9]+):([A-Za-z][A-Za-z0-9-]*)?-->")
_CLOSED: Final = re.compile(r"[0-9]+-->")
_COMMENT: Final = len("<!--")
_STALE: Final = re.compile(
    r"<!--(?P<id>b-[0-9a-f]{8}-[0-9]+):[A-Za-z0-9-]*-->"
    r'<[A-Za-z][^>]* id="(?P=id)" data-next-boundary="(?P=id)"'
)
_SITE: Final = "a deferred boundary"
_CAPTURED: Final = (
    "The response holds the loading wrapper of a deferred boundary from an earlier "
    "response, so a tag that stores rendered output, such as {% cache %}, captured "
    "it. Render the component inline there, defer='inline' at the call site."
)

type Run = Callable[[BoundarySession[Delta]], Iterable[Iterable[Outcome[Delta]]]]
"""What runs the jobs of a session and yields their outcomes in batches."""

type Build = Callable[[Piece], bytes]
"""What serialises one settled piece as the frame its stream writes."""


type _Opening = tuple[int, int, str, str | None]


def _marks(text: str, lead: str, rest: re.Pattern[str]) -> Iterator[re.Match[str]]:
    """Yield what `rest` matches right after each `lead` in `text`, in order.

    The seal is in `lead`, a plain search, so no pattern is compiled per response.
    """
    at = text.find(lead)
    while at >= 0:
        found = rest.match(text, at + len(lead))
        if found is None:
            at = text.find(lead, at + 1)
            continue
        yield found
        at = text.find(lead, found.end())


def _openings(text: str, seal: str) -> Iterator[_Opening]:
    """Yield the bounds, id and tag of each opening marker `seal` sealed in `text`."""
    lead = f"<!--b-{seal}-"
    for found in _marks(text, lead, _OPENED):
        start = found.start() - len(lead)
        yield start, found.end(), text[start + _COMMENT : found.end(1)], found[2]


def _identity(ident: str, key: str, source: str | None) -> str:
    named = "" if source is None else f' data-next-boundary-source="{escape(source)}"'
    return f'{identity(ident)} data-next-boundary-key="{escape(key)}"{named}'


def _state(kind: ErrorKind | None, failure: BoundaryError | None = None) -> str:
    """Return the state attributes, the digest and the detail shown under `DEBUG`."""
    if kind is None:
        return ' data-next-state="ready"'
    state = f' data-next-state="error" data-next-error="{kind.value}"'
    if isinstance(failure, DebugBoundaryError):
        state += f' data-next-error-digest="{escape(failure.digest)}"'
        if failure.message:
            state += f' data-next-error-detail="{escape(failure.message)}"'
    return state


def _element(tag: str | None, attributes: str, body: str) -> str:
    """Return `body` inside a `tag` wrapper, or with the attributes on its one root."""
    if tag is not None:
        return f"<{tag}{attributes}>{body}</{tag}>"
    if not body:
        return f"<template{attributes}></template>"
    return adopt(attributes, body, site=_SITE)


def _closed(
    tag: str | None,
    attributes: str,
    body: str,
    kind: ErrorKind | None,
    failure: BoundaryError | None,
    retry: str = "",
) -> str:
    """Return the settled wrapper, an error UI marked as an inline boundary is.

    `retry` holds the token attributes an error wrapper offers its retry with.
    """
    if failure is None:
        return _element(tag, attributes + _state(kind), body)
    attributes += retry
    if tag is None and not body:
        return f"<template{attributes}{_state(failure.kind, failure)}></template>"
    return failed(tag, attributes, failure, body, site=_SITE)


def shell(
    ident: str,
    tag: str | None,
    key: str,
    source: str | None,
    loading: str,
    failure: BoundaryError | None = None,
) -> str:
    """Return the loading wrapper of a queued job, between markers naming its id.

    A `failure` is a loading UI its site refused, which settles the wrapper as an error.
    """
    attributes = _identity(ident, key, source)
    if failure is None:
        element = _element(tag, attributes + _LOADING, loading)
    else:
        element = _closed(tag, attributes, loading, failure.kind, failure)
    return f"<!--{ident}:{tag or ''}-->{element}<!--/{ident}-->"


def settled(
    ident: str,
    tag: str | None,
    key: str,
    source: str | None,
    html: str,
    failure: BoundaryError | None,
    retry: str = "",
) -> str:
    """Return the wrapper of a boundary rendered in place, ready or failed."""
    kind = None if failure is None else failure.kind
    return _closed(tag, _identity(ident, key, source), html, kind, failure, retry)


def idle(
    ident: str,
    tag: str | None,
    key: str,
    source: str | None,
    trip: tuple[DeferMode, str],
    loading: str,
    failure: BoundaryError | None = None,
) -> str:
    """Return the wrapper of a client-trip boundary, its token and its loading UI.

    `trip` pairs the mode with the token attributes, and a wrapper adopting the root
    of its loading UI says so, since the browser swaps that root for the component's.
    """
    mode, token = trip
    attributes = f'{_identity(ident, key, source)} data-next-defer="{mode.value}"'
    attributes += token
    if failure is not None:
        return _closed(tag, attributes, loading, failure.kind, failure)
    if tag is None:
        attributes += _ADOPTED
    return _element(tag, attributes + _IDLE, loading)


def wrappers(html: str, seal: str) -> dict[str, tuple[str | None, str]]:
    """Return the tag and the identity attributes of each loading wrapper in `html`.

    It reads the markers of the response sealed by `seal`, so it runs before `strip`.
    """
    found: dict[str, tuple[str | None, str]] = {}
    for _, opened, ident, tag in _openings(html, seal):
        start = html.find(f' id="{ident}"', opened)
        end = -1 if start < 0 else html.find(_STATE, start)
        if end >= 0:
            found[ident] = (tag or None, html[start:end])
    return found


def strip(shell_html: str, seal: str) -> str:
    """Return the shell without the markers of the response sealed by `seal`."""
    closer = f"<!--/b-{seal}-"
    spans = [(start, end) for start, end, _, _ in _openings(shell_html, seal)]
    spans += [
        (found.start() - len(closer), found.end())
        for found in _marks(shell_html, closer, _CLOSED)
    ]
    if not spans:
        return shell_html
    spans.sort()
    parts: list[str] = []
    position = 0
    for start, end in spans:
        parts.append(shell_html[position:start])
        position = end
    parts.append(shell_html[position:])
    return "".join(parts)


class _Assembly:
    """The outcomes of one response and what splicing them found.

    Only an opening marker of this response naming a queued job splices, once, so a
    marker an echo or an older response wrote stays the text it is.
    """

    __slots__ = ("collector", "degraded", "intended", "outcomes", "seal")

    def __init__(
        self,
        outcomes: dict[str, Outcome[Delta]],
        collector: StaticCollector | None,
        seal: str,
    ) -> None:
        """Hold the outcomes by id, nothing found yet."""
        self.outcomes = outcomes
        self.collector = collector
        self.seal = seal
        self.degraded = False
        self.intended: Exception | None = None

    def splice(self, text: str) -> str:
        """Return `text` with every loading wrapper replaced by its settled one."""
        parts: list[str] = []
        position = 0
        for start, opened, ident, tag in _openings(text, self.seal):
            if start < position:
                continue
            closing = f"<!--/{ident}-->"
            end = text.find(closing, opened)
            outcome = self.outcomes.pop(ident, None)
            if end < 0 or outcome is None:
                continue
            parts.append(text[position:start])
            segment = text[opened:end]
            parts.append(self._settle(tag, ident, segment, outcome))
            position = end + len(closing)
        parts.append(text[position:])
        return "".join(parts)

    def _settle(
        self, tag: str | None, ident: str, segment: str, outcome: Outcome[Delta]
    ) -> str:
        """Return the settled wrapper of one outcome, its assets merged first."""
        rendered = outcome.rendered
        self._take(rendered)
        start = segment.index(f' id="{ident}"')
        attributes = segment[start : segment.index(_STATE, start)]
        body = self.splice(rendered.html)
        return _closed(
            tag, attributes, body, rendered.kind, rendered.failure, rendered.retry
        )

    def _take(self, rendered: Rendered[Delta]) -> None:
        """Merge the assets of one outcome and replay its writes, in document order."""
        if self.collector is not None:
            rendered.delta.apply(self.collector)
        for replay in rendered.replay:
            replay()
        if rendered.status is not None:
            mark_degraded(rendered.status)
        if rendered.degraded or rendered.kind is not None:
            self.degraded = True
        if self.intended is None:
            self.intended = rendered.intended


def _stale(shell_html: str, seal: str) -> None:
    """Warn when the shell holds a loading wrapper another response sealed."""
    own = f"b-{seal}-"
    if any(not found["id"].startswith(own) for found in _STALE.finditer(shell_html)):
        _failures.warn(("captured",), _CAPTURED)


def assemble(
    shell_html: str,
    outcomes: Iterable[Outcome[Delta]],
    collector: StaticCollector | None,
    *,
    seal: str,
) -> str:
    """Return the shell with every outcome in its wrapper, nested ones inside parents.

    The assets merge before injection, and an intended exception of a job raises here.
    """
    _stale(shell_html, seal)
    held = {outcome.id: outcome for outcome in outcomes}
    assembly = _Assembly(held, collector, seal)
    html = assembly.splice(shell_html)
    if assembly.intended is not None:
        raise assembly.intended
    if assembly.degraded:
        mark_degraded()
    return html


def prune(session: BoundarySession[Delta], shell_html: str) -> BoundarySession[Delta]:
    """Return the session without the jobs whose wrapper the shell no longer holds.

    A failed subtree discards its markup, so a job queued inside it never runs.
    """
    present = {ident for _, _, ident, _ in _openings(shell_html, session.seal)}
    kept = [job for job in session.jobs if job.id in present]
    if len(kept) == len(session.jobs):
        return session
    pruned = BoundarySession[Any](
        session.caps, seal=session.seal, deadline=session.deadline
    )
    pruned.facts = session.facts
    pruned.threads = session.threads
    pruned.connections = session.connections
    pruned.snapshot = session.snapshot
    pruned.scope = session.scope
    pruned.ledger = session.ledger
    for job in kept:
        pruned.queue(job)
    return pruned


def _settled(job: Job[Delta], expired: frozenset[str]) -> Iterator[Outcome[Delta]]:
    """Run `job` here, then its nested jobs, a job out of time settling as `timeout`."""
    if job.id in expired or time.monotonic() >= job.deadline:
        yield Outcome(job.id, job.fallback(ErrorKind.TIMEOUT))
        return
    body = job.run
    if body is None:
        arun = cast("Callable[[], Coroutine[Any, Any, Rendered[Delta]]]", job.arun)
        rendered = run_bridged(arun)
    else:
        rendered = body()
    yield Outcome(job.id, rendered)
    for nested in rendered.nested:
        yield from _settled(nested, expired)


def run_sequentially(
    session: BoundarySession[Delta],
) -> Iterator[tuple[Outcome[Delta], ...]]:
    """Run every job on the calling thread in document order, one batch per job."""
    expired = frozenset(job.id for job in session.plan().expired)
    for job in session.jobs:
        yield tuple(_settled(job, expired))


def finish(
    shell_html: str,
    session: BoundarySession[Delta],
    collector: StaticCollector | None,
    run: Run,
) -> str:
    """Drop the jobs the shell lost, run the rest through `run`, assemble the page."""
    live = prune(session, shell_html)
    outcomes = [outcome for batch in run(live) for outcome in batch]
    return assemble(shell_html, outcomes, collector, seal=session.seal)


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

    def lane(self) -> str | None:
        """Return the request-lane rule pinning a job, read in the state jobs run in."""


@dataclass(frozen=True, slots=True)
class Admitted:
    """The stream slot a view reserved and the facts it reports of the stream it admits.

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


def _seal_of(ident: str) -> str:
    """Return the seal of the response a wire id was minted in, `b-<seal>-<n>`."""
    return ident[2 : ident.rindex("-")]


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

    def written(self, piece: Piece) -> dict[str, object]:
        """Replay the writes of the job for the writes after it, then map its own."""
        for replay in piece.outcome.rendered.replay:
            replay()
        return self.payload(piece)

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
            if isinstance(rendered.failure, DebugBoundaryError):
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
        seal = _seal_of(ident)
        self._wrappers.update(wrappers(rendered.html, seal))
        body = strip(rendered.html, seal)
        tag, attributes = self._wrappers.get(ident, ("div", ""))
        element = _closed(
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
                _failures.warn(("noted", step.key), _NOTED, step.key)
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

    def batch(self, pieces: Iterable[Piece]) -> str:
        """Return the chunk scripts of one batch, in the order the batch holds them."""
        return "".join(self.script(piece) for piece in pieces)

    def end(self) -> str:
        """Return the script marking that no chunk follows."""
        return self._builder.chunk_script(json.dumps(keys.CHUNK_END), nonce=self._nonce)

    def script(self, piece: Piece) -> str:
        """Return the chunk script of one piece."""
        payload = json.dumps(self.written(piece), separators=(",", ":"))
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
        admitted: Admitted,
        *,
        sender: type,
        partial: bool = False,
    ) -> None:
        """Hold the jobs and take over the slot of `admitted`, nothing run yet."""
        self.request = request
        self.batches = batches
        self.admitted = admitted
        self.slot = admitted.slot
        if self.slot is not None:
            self.slot.bind(self)
        self.page_path = admitted.page_path
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
        data = b""
        for piece in pieces:
            framed = self.framed(piece, build)
            data += self.fallback(piece, build) if framed is None else framed
        return data

    async def aframes(self, pieces: Iterable[Piece], build: Build) -> bytes:
        """Return what `frames` does, an error UI rendered on the request thread."""
        data = b""
        for piece in pieces:
            framed = self.framed(piece, build)
            if framed is None:
                fallback = sync_to_async(self.fallback, thread_sensitive=True)
                framed = await fallback(piece, build)
            data += framed
        return data

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
        admitted = self.admitted
        return {
            "page_path": self.page_path,
            "delivery": admitted.delivery,
            "reason": admitted.reason,
            "boundaries": admitted.boundaries,
            "request_lane": self.batches.lane(),
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
        admitted: Admitted,
    ) -> None:
        """Hold the shell encoded, so text no byte spells fails before the headers."""
        head, tail = document.head.encode(), document.tail.encode()
        super().__init__(request, batches, admitted, sender=StreamingPageResponse)
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
        admitted: Admitted | None = None,
    ) -> None:
        """Hold the shell, the jobs and the writer, writing nothing yet."""
        writer = _Writer(request, document, batches, chunks, admitted or Admitted())
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
    "Admitted",
    "Batches",
    "Chunks",
    "Document",
    "Lifetime",
    "Payloads",
    "Piece",
    "Run",
    "StreamingPageResponse",
    "apart",
    "assemble",
    "dress",
    "finish",
    "prune",
    "release_connections",
    "run_sequentially",
    "settled",
    "shell",
    "strip",
    "wrappers",
]
