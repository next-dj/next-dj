"""The policy deciding once per request how a page delivers its deferred calls."""

import enum
import logging
import re
import threading
import weakref
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, override

from django.http import HttpRequest

from next.caches import FORK, pure_cache
from next.compression.manager import compression_manager
from next.conf.defer import backend_option, defer_count, defer_option, entry_options
from next.conf.router import router_enabled
from next.pages.responses import shared_render
from next.partial.headers import accepts_event_stream, partial_intent
from next.partial.keys import NOSCRIPT_PARAM
from next.requests import PrefetchDepth
from next.static import ScriptInjectionPolicy, get_static_manager
from next.static.nonce import CSP_MIDDLEWARE
from next.utils import middleware_installed

from .connections.facts import server_of
from .markers import Delivery, ServerKind
from .probe import ServerFacts, server_facts
from .signals import page_stream_declined


logger = logging.getLogger("next.defer")


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


class DeliveryReason(enum.StrEnum):
    """The row of the shipped policy and the stream slot check that chose a delivery."""

    HEAD = "head"
    PREFETCH = "prefetch"
    STREAM = "stream"
    METHOD = "method"
    BOT = "bot"
    NOSCRIPT = "noscript"
    CACHE = "cache"
    GZIP = "gzip"
    BUFFERED = "buffered"
    SYNC_WORKER = "sync_worker"
    RUNTIME = "runtime"
    CAPACITY = "capacity"
    PARTIAL = "partial"
    OPTION = "option"


_INLINE: Final = (
    "A response with deferred boundaries was assembled inline rather than streamed, "
    "reason %s, server %s. Only the first one of each reason is logged, "
    "page_stream_declined reports each."
)
_DEPLOYED: Final = (
    "A response with deferred boundaries was assembled inline rather than streamed, "
    "reason %s, server %s, which holds for every page this process serves. %s "
    "Only the first one of each reason is logged, page_stream_declined reports each."
)
_CURES: Final[Mapping[str, str]] = {
    DeliveryReason.GZIP: (
        "Replace django.middleware.gzip.GZipMiddleware with "
        "next.middleware.CompressionMiddleware under NEXT_FRAMEWORK['COMPRESSION']"
        "['ENABLED'], as GZipMiddleware buffers a stream below Django 6.1."
    ),
    DeliveryReason.BUFFERED: (
        "The server does not deliver a write as it happens, and "
        "NEXT_FRAMEWORK['DEFER']['SERVER']['OPTIONS']['INCREMENTAL'] states it "
        "where the detection is wrong."
    ),
    DeliveryReason.SYNC_WORKER: (
        "The server runs one request at a time, so serve the project from threaded "
        "workers or ASGI, or set NEXT_FRAMEWORK['DEFER']['DELIVERY']['OPTIONS']"
        "['SYNC_WORKERS'] to 'stream'."
    ),
    DeliveryReason.RUNTIME: (
        "NEXT_JS_OPTIONS['policy'] of 'disabled' keeps the client runtime that applies "
        "a chunk off the page."
    ),
    DeliveryReason.CAPACITY: (
        "The process holds the open streams NEXT_FRAMEWORK['DEFER']['MAX_STREAMS'] "
        "allows, which 'auto' sizes under WSGI from REQUEST_CONCURRENCY and the "
        "request threads seen."
    ),
}
"""The reasons of the deployment rather than the request, each with its cure."""


class StreamSlot:
    """One slot of `MAX_STREAMS`, given back once by `release` or once nothing holds it.

    The finalizer belongs to the slot, so a render or a response dropped unread leaks no
    slot and no holder registers one of its own.
    """

    __slots__ = ("__weakref__", "_give")

    def __init__(self, slots: "StreamSlots", generation: int) -> None:
        """Hold one slot of `slots`, taken in its `generation`."""
        give = weakref.finalize(self, slots.give, generation)
        give.atexit = False
        self._give = give

    @property
    def held(self) -> bool:
        """Return whether the slot is still taken."""
        return self._give.alive

    def release(self) -> None:
        """Give the slot back, a later call doing nothing."""
        self._give()


class StreamSlots:
    """Count the open page and partial streams of this process against `MAX_STREAMS`.

    The lock is reentrant, as the collector may finalize a slot inside a held count.
    """

    __slots__ = ("_count", "_generation", "_lock")

    def __init__(self) -> None:
        """Start with no open stream."""
        self._lock = threading.RLock()
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
        self._lock = threading.RLock()
        self._count = 0
        self._generation += 1


open_streams: Final = StreamSlots()
"""The open page and partial streams of this process."""

FORK.attach(open_streams.forget)


class RequestThreads:
    """The live request threads of this process that asked how a page delivers.

    A threaded server keeps a thread per concurrent request, so they count its threads.
    """

    __slots__ = ("_lock", "_seen")

    def __init__(self) -> None:
        """Start with no thread seen."""
        self.forget()

    def forget(self) -> None:
        """Forget every thread, as a forked child runs none of its parent's."""
        self._lock = threading.Lock()
        self._seen: weakref.WeakSet[threading.Thread] = weakref.WeakSet()

    def note(self) -> None:
        """Count the calling thread among the request threads."""
        thread = threading.current_thread()
        with self._lock:
            self._seen.add(thread)

    def count(self) -> int:
        """Return how many of the threads seen still run."""
        with self._lock:
            return sum(thread.is_alive() for thread in self._seen)


request_threads: Final = RequestThreads()
"""The request threads this process has served deferred pages on."""

FORK.attach(request_threads.forget)


def stream_limit(server: ServerKind) -> int:
    """Return how many streams may be open at once, `"auto"` derived from the server.

    It is four per pool worker under ASGI, else half the request threads seen or set.
    """
    value = defer_option("MAX_STREAMS")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if server is ServerKind.ASGI:
        return _AUTO_PER_WORKER * defer_count("EXECUTOR", "OPTIONS", "MAX_WORKERS")
    threads = max(defer_count("REQUEST_CONCURRENCY"), request_threads.count())
    return max(1, threads // 2)


@dataclass(frozen=True, slots=True)
class DeliveryFacts:
    """What one request tells the policy, read on the thread that runs the view."""

    server: ServerKind
    method: str
    probe: ServerFacts
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
    noscript: bool = False


@dataclass(frozen=True, slots=True)
class DeliveryDecision:
    """How one response delivers its deferred boundaries, and the row that chose it.

    A project policy names its own rows, the shipped one a `DeliveryReason`.
    """

    delivery: Delivery
    reason: str


def delivery_facts(request: HttpRequest) -> DeliveryFacts:
    """Return the facts of `request` the delivery policy reads."""
    coding, buffering = compression_manager.document_coding(request)
    probe = server_facts(request)
    server = server_of(request, probe)
    if server is not ServerKind.ASGI:
        request_threads.note()
    intent = partial_intent(request)
    return DeliveryFacts(
        server=server,
        method=request.method or _GET,
        probe=probe,
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
        prefetch=intent.prefetch is PrefetchDepth.SHELL and router_enabled(),
        noscript=request.GET.get(NOSCRIPT_PARAM) == "1",
    )


class DeliveryPolicy(ABC):
    """Decide how a page delivers its deferred boundaries, once per request."""

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Keep the `OPTIONS` of the settings entry."""
        self.options: Mapping[str, object] = entry_options(config)

    @abstractmethod
    def delivery(self, request: HttpRequest, facts: DeliveryFacts) -> DeliveryDecision:
        """Return how the response to `request` delivers its deferred boundaries."""

    def streams_partials(self, request: HttpRequest) -> bool:
        """Return whether a partial request this server answers may ever stream.

        The page tells the runtime, which asks for a stream only when it may come.
        """
        del request
        return True


@pure_cache(16)
def _bots(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


@pure_cache(_AGENTS)
def _is_bot(pattern: str, agent: str) -> bool:
    """Whether `agent` matches `pattern`, held per agent, as clients send few."""
    return _bots(pattern).search(agent) is not None


class DefaultDeliveryPolicy(DeliveryPolicy):
    """Answer `HEAD` with the shell and stream every other request no row holds back.

    The reason names the first row that holds. A browser prefetch is assembled whole,
    and so is a partial request whose client reads no `text/event-stream`.
    """

    def _bot(self, agent: str) -> bool:
        pattern = backend_option(self.options, "DELIVERY", "BOTS")
        text = pattern if isinstance(pattern, str) and pattern else BOTS
        return bool(agent) and _is_bot(text, agent)

    def _reason(self, facts: DeliveryFacts) -> DeliveryReason | None:
        """Return why the response is assembled inline, `None` when it streams.

        The rows are read in order, so the agent is matched only past the first three.
        """
        if facts.speculative:
            return DeliveryReason.PREFETCH
        if facts.method != _GET:
            return DeliveryReason.METHOD
        if facts.noscript:
            return DeliveryReason.NOSCRIPT
        if self._bot(facts.agent):
            return DeliveryReason.BOT
        rows = (
            (DeliveryReason.CACHE, facts.shared_cache),
            (DeliveryReason.GZIP, facts.compression == "gzip" and facts.buffering),
            (DeliveryReason.BUFFERED, not facts.probe.incremental),
            (
                DeliveryReason.SYNC_WORKER,
                not facts.probe.concurrent
                and backend_option(self.options, "DELIVERY", "SYNC_WORKERS")
                == "inline",
            ),
            (DeliveryReason.RUNTIME, not facts.runtime),
            (DeliveryReason.CAPACITY, not facts.capacity),
            (DeliveryReason.PARTIAL, facts.partial and not facts.accepts_stream),
            (
                DeliveryReason.OPTION,
                backend_option(self.options, "DELIVERY", "STREAM") is False,
            ),
        )
        for reason, holds in rows:
            if holds:
                return reason
        return None

    @override
    def streams_partials(self, request: HttpRequest) -> bool:
        """Return false under `STREAM=False`, behind a buffer or on an inline worker."""
        if backend_option(self.options, "DELIVERY", "STREAM") is False:
            return False
        probe = server_facts(request)
        if not probe.incremental:
            return False
        return (
            probe.concurrent
            or backend_option(self.options, "DELIVERY", "SYNC_WORKERS") != "inline"
        )

    @override
    def delivery(self, request: HttpRequest, facts: DeliveryFacts) -> DeliveryDecision:
        """Return `SHELL` for `HEAD`, `PREFETCH` for a shell prefetch, else a stream.

        A shell prefetch is decided before every other row, so it never opens a stream.
        """
        if facts.method == _HEAD:
            return DeliveryDecision(Delivery.SHELL, DeliveryReason.HEAD)
        if facts.prefetch:
            return DeliveryDecision(Delivery.PREFETCH, DeliveryReason.PREFETCH)
        reason = self._reason(facts)
        if reason is None:
            return DeliveryDecision(Delivery.STREAM, DeliveryReason.STREAM)
        return DeliveryDecision(Delivery.INLINE, reason)


_NOTICES: Final = 64
"""The most reasons a process logs, as a project policy may name any."""


class InlineNotice:
    """Let the first inline response of each reason through, again after a fork."""

    __slots__ = ("_lock", "_spent")

    def __init__(self) -> None:
        """Start before the first response."""
        self.forget()

    def forget(self) -> None:
        """Let the next response of each reason through, as a reload or a fork must."""
        self._lock = threading.Lock()
        self._spent: set[str] = set()

    def take(self, reason: str) -> bool:
        """Return whether the caller is the first of `reason` since the last forget."""
        if reason in self._spent:
            return False
        with self._lock:
            if reason in self._spent or len(self._spent) >= _NOTICES:
                return False
            self._spent.add(reason)
        return True


inline_notice: Final = InlineNotice()
"""The once-per-process and per-reason log of a response assembled inline."""

FORK.attach(inline_notice.forget)


def declined(
    request: HttpRequest, delivered: DeliveryDecision, facts: DeliveryFacts
) -> None:
    """Announce a response whose deferred jobs are assembled inline, logging the first.

    A reason of the deployment logs a warning naming its cure, one of the request info.
    """
    server = facts.probe.reason
    reason = str(delivered.reason)
    if inline_notice.take(reason):
        cure = _CURES.get(reason)
        if cure is None:
            logger.info(_INLINE, reason, server)
        else:
            logger.warning(_DEPLOYED, reason, server, cure)
    if page_stream_declined.has_listeners(DeliveryPolicy):
        page_stream_declined.send_robust(
            sender=DeliveryPolicy,
            delivery=delivered.delivery,
            reason=delivered.reason,
            server_reason=server,
            partial=facts.partial,
            request=request,
        )


__all__ = [
    "BOTS",
    "DefaultDeliveryPolicy",
    "DeliveryDecision",
    "DeliveryFacts",
    "DeliveryPolicy",
    "DeliveryReason",
    "RequestThreads",
    "StreamSlot",
    "StreamSlots",
    "declined",
    "delivery_facts",
    "open_streams",
    "request_threads",
    "stream_limit",
]
