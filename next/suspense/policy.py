"""The policy deciding once per request how a page delivers its deferred calls."""

import functools
import logging
import re
import threading
import weakref
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Final, override

from django.http import HttpRequest

from next.compression import compression_manager
from next.conf.defer import defer_count, defer_option
from next.conf.router import router_enabled
from next.defer import Delivery
from next.pages.responses import shared_render
from next.partial.headers import accepts_event_stream, partial_intent
from next.static import ScriptInjectionPolicy, get_static_manager
from next.static.nonce import CSP_MIDDLEWARE
from next.suspense.connections.facts import server_of
from next.suspense.markers import Server
from next.suspense.signals import page_stream_declined
from next.suspense.transport import TransportFacts, transport_of
from next.utils import after_fork, middleware_installed


logger = logging.getLogger("next.suspense")


BOTS: Final = (
    r"bot|crawl|spider|slurp|facebookexternalhit|embedly|quora link preview"
    r"|whatsapp|telegrambot|lighthouse|headlesschrome|preview"
)
"""The pattern a `BOTS` of `None` matches user agents against, case-insensitively."""

_AGENT_CHARS: Final = 512
_AGENTS: Final = 1024
_HEAD: Final = "HEAD"
_GET: Final = "GET"
_PREFETCH: Final = "prefetch"
_AUTO_PER_WORKER: Final = 4
_STREAMING: Final = "streaming"
_INLINE: Final = (
    "A response with deferred boundaries was assembled inline rather than streamed, "
    "reason %s, transport %s. Only the first one is logged, page_stream_declined "
    "reports each."
)


class StreamSlot:
    """The hold one stream has on a slot, given back once however the stream ends.

    A holder bound through `bind` gives it back when collected while still holding it.
    """

    __slots__ = ("_finalizer", "_generation", "_held", "_lock", "_slots")

    def __init__(self, slots: "StreamSlots", generation: int) -> None:
        """Hold one slot of `slots`, taken in its `generation`."""
        self._slots = slots
        self._generation = generation
        self._lock = threading.Lock()
        self._held = True
        self._finalizer: weakref.finalize[[], object] | None = None

    @property
    def held(self) -> bool:
        """Return whether the slot is still taken."""
        return self._held

    def bind(self, owner: object) -> None:
        """Give the slot back once `owner` is collected, replacing an earlier owner."""
        finalizer = weakref.finalize(owner, self.release)
        finalizer.atexit = False
        with self._lock:
            earlier = self._finalizer
            if self._held:
                self._finalizer = finalizer
            else:
                earlier = finalizer
        if earlier is not None:
            earlier.detach()

    def release(self) -> None:
        """Give the slot back, a later call doing nothing."""
        with self._lock:
            if not self._held:
                return
            self._held = False
            finalizer = self._finalizer
            self._finalizer = None
        if finalizer is not None:
            finalizer.detach()
        self._slots.give(self._generation)


class StreamSlots:
    """Count the open page and partial streams of this process against `MAX_STREAMS`.

    The slot the policy reserves as it decides is owned by the stream it admits.
    """

    __slots__ = ("_count", "_generation", "_lock")

    def __init__(self) -> None:
        """Start with no open stream."""
        self._lock = threading.Lock()
        self._count = 0
        self._generation = 0

    @property
    def open(self) -> int:
        """Return how many streams hold a slot now."""
        return self._count

    def acquire(self, limit: int) -> StreamSlot | None:
        """Take a slot when fewer than `limit` are held, `None` when none is free."""
        with self._lock:
            if self._count >= limit:
                return None
            self._count += 1
            generation = self._generation
        return StreamSlot(self, generation)

    def give(self, generation: int) -> None:
        """Give back one slot taken in `generation`, ignoring one from before a fork."""
        with self._lock:
            if generation == self._generation and self._count > 0:
                self._count -= 1

    def forget(self) -> None:
        """Drop every slot, as a forked child holds none of its parent's streams."""
        self._lock = threading.Lock()
        self._count = 0
        self._generation += 1


open_streams: Final = StreamSlots()
"""The open page and partial streams of this process."""

after_fork(open_streams.forget)


def stream_limit(server: Server) -> int:
    """Return how many streams may be open at once, `"auto"` derived from the server.

    It is four per pool worker under ASGI and half the request threads otherwise.
    """
    value = defer_option("MAX_STREAMS")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if server is Server.ASGI:
        return _AUTO_PER_WORKER * defer_count("EXECUTOR", "OPTIONS", "MAX_WORKERS")
    return max(1, defer_count("REQUEST_CONCURRENCY") // 2)


_streamed: ContextVar[bool] = ContextVar("next_streamed", default=False)


@contextmanager
def streaming() -> Iterator[None]:
    """Report a transport serving one request at a time as concurrent inside the block.

    The test client runs a request on a single-threaded worker, which never streams.
    """
    token = _streamed.set(True)
    try:
        yield
    finally:
        _streamed.reset(token)


@dataclass(frozen=True, slots=True)
class PageFacts:
    """What one request tells the policy, read on the thread that runs the view.

    `compression` is the coding the stream goes out in, `buffering` whether a middleware
    buffers it, `runtime` whether the runtime loads, `capacity` whether a slot is free,
    and `prefetch` whether the client router asks for the shell alone, no job run.
    """

    server: Server
    method: str
    transport: TransportFacts
    compression: str | None = None
    buffering: bool = False
    csp: bool = False
    shared_cache: bool = False
    partial: bool = False
    accepts_stream: bool = False
    agent: str = ""
    runtime: bool = True
    speculative: bool = False
    capacity: bool = True
    prefetch: bool = False


@dataclass(frozen=True, slots=True)
class Delivered:
    """How one response delivers its deferred boundaries, and the row that chose it."""

    delivery: Delivery
    reason: str


def transported(request: HttpRequest) -> TransportFacts:
    """Return how the response to `request` reaches its client, concurrent in a test."""
    facts = transport_of(request)
    if facts.concurrent or not _streamed.get():
        return facts
    return replace(facts, concurrent=True, concurrent_reason=_STREAMING)


def page_facts(request: HttpRequest) -> PageFacts:
    """Return the facts of `request` the delivery policy reads."""
    coding, buffering = compression_manager.document_coding(request)
    transport = transported(request)
    server = server_of(request, transport)
    intent = partial_intent(request)
    return PageFacts(
        server=server,
        method=request.method or _GET,
        transport=transport,
        compression=coding,
        buffering=buffering,
        csp=any(map(middleware_installed, CSP_MIDDLEWARE)),
        shared_cache=shared_render(request),
        partial=intent.partial,
        accepts_stream=accepts_event_stream(request),
        agent=request.META.get("HTTP_USER_AGENT", "")[:_AGENT_CHARS],
        runtime=get_static_manager().script_builder().policy
        is not ScriptInjectionPolicy.DISABLED,
        speculative=_PREFETCH in request.META.get("HTTP_SEC_PURPOSE", ""),
        capacity=open_streams.open < stream_limit(server),
        prefetch=intent.prefetch and not intent.full and router_enabled(),
    )


class StreamPolicy(ABC):
    """Decide how a page delivers its deferred boundaries, once per request."""

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Keep the `OPTIONS` of the settings entry."""
        options = (config or {}).get("OPTIONS")
        self.options: Mapping[str, object] = (
            options if isinstance(options, Mapping) else {}
        )

    @abstractmethod
    def delivery(self, request: HttpRequest, facts: PageFacts) -> Delivered:
        """Return how the response to `request` delivers its deferred boundaries."""

    def streams_partials(self, request: HttpRequest) -> bool:
        """Return whether a partial request this server answers may ever stream.

        The page tells the runtime, which asks for a stream only when it may come.
        """
        del request
        return True


@functools.lru_cache(maxsize=16)
def _bots(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


@functools.lru_cache(maxsize=_AGENTS)
def _is_bot(pattern: str, agent: str) -> bool:
    """Whether `agent` matches `pattern`, held per agent, as clients send few."""
    return _bots(pattern).search(agent) is not None


class DefaultStreamPolicy(StreamPolicy):
    """Answer `HEAD` with the shell and stream every other request no row holds back.

    The reason names the first row that holds. A browser prefetch is assembled whole,
    and so is a partial request whose client reads no `text/event-stream`.
    """

    def _option(self, key: str) -> object:
        if key in self.options:
            return self.options[key]
        return defer_option("POLICY", "OPTIONS", key)

    def _bot(self, agent: str) -> bool:
        pattern = self._option("BOTS")
        text = pattern if isinstance(pattern, str) and pattern else BOTS
        return bool(agent) and _is_bot(text, agent)

    def _reason(self, facts: PageFacts) -> str | None:
        """Return why the response is assembled inline, `None` when it streams.

        The rows are read in order, so the agent is matched only past the first two.
        """
        if facts.speculative:
            return _PREFETCH
        if facts.method != _GET:
            return "method"
        if self._bot(facts.agent):
            return "bot"
        rows = (
            ("cache", facts.shared_cache),
            ("gzip", facts.compression == "gzip" and facts.buffering),
            ("buffered", not facts.transport.incremental),
            (
                "sync-worker",
                not facts.transport.concurrent
                and self._option("SYNC_WORKERS") == "inline",
            ),
            ("runtime", not facts.runtime),
            ("capacity", not facts.capacity),
            ("partial", facts.partial and not facts.accepts_stream),
            ("option", self._option("STREAM") is False),
        )
        return next((reason for reason, holds in rows if holds), None)

    @override
    def streams_partials(self, request: HttpRequest) -> bool:
        """Return false under `STREAM=False`, behind a buffer or on an inline worker."""
        if self._option("STREAM") is False:
            return False
        transport = transported(request)
        if not transport.incremental:
            return False
        return transport.concurrent or self._option("SYNC_WORKERS") != "inline"

    @override
    def delivery(self, request: HttpRequest, facts: PageFacts) -> Delivered:
        """Return `SHELL` for `HEAD`, `PREFETCH` for a shell prefetch, else a stream.

        A shell prefetch is decided before every other row, so it never opens a stream.
        """
        if facts.method == _HEAD:
            return Delivered(Delivery.SHELL, "head")
        if facts.prefetch:
            return Delivered(Delivery.PREFETCH, _PREFETCH)
        reason = self._reason(facts)
        if reason is None:
            return Delivered(Delivery.STREAM, "stream")
        return Delivered(Delivery.INLINE, reason)


class InlineNotice:
    """Let the first response assembled inline through, again in a forked child."""

    __slots__ = ("_lock", "_spent")

    def __init__(self) -> None:
        """Start before the first response."""
        self.forget()

    def forget(self) -> None:
        """Let the next response through, as a reload or a forked child must."""
        self._lock = threading.Lock()
        self._spent = False

    def take(self) -> bool:
        """Return whether this caller is the first one since the last `forget`."""
        if self._spent:
            return False
        with self._lock:
            spent, self._spent = self._spent, True
        return not spent


inline_notice: Final = InlineNotice()
"""The once-per-process log of a response assembled inline."""

after_fork(inline_notice.forget)


def declined(request: HttpRequest, delivered: Delivered, facts: PageFacts) -> None:
    """Announce a response whose deferred jobs are assembled inline, logging the first.

    The log names the fact in platform logs with no setup, the signal counts each one.
    """
    transport = facts.transport.reason
    if inline_notice.take():
        logger.info(_INLINE, delivered.reason, transport)
    if page_stream_declined.has_listeners(StreamPolicy):
        page_stream_declined.send_robust(
            sender=StreamPolicy,
            delivery=delivered.delivery,
            reason=delivered.reason,
            transport_reason=transport,
            partial=facts.partial,
            request=request,
        )


__all__ = [
    "BOTS",
    "DefaultStreamPolicy",
    "Delivered",
    "PageFacts",
    "StreamPolicy",
    "StreamSlot",
    "StreamSlots",
    "declined",
    "open_streams",
    "page_facts",
    "stream_limit",
    "streaming",
    "transported",
]
