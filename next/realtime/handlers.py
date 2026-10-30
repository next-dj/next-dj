"""Registry of the upstream handlers a client event on a granted topic reaches."""

import asyncio
import functools
import inspect
import os
import re
import threading
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from importlib import import_module
from types import CodeType, MappingProxyType, TracebackType
from typing import Final, Self, TypeGuard, cast

from asgiref.sync import iscoroutinefunction
from django.utils.module_loading import autodiscover_modules

from next.conf.scopes import scope_value
from next.utils import after_fork

from .lifecycle import areceived
from .pool import run_pooled


type Handler = Callable[[Upstream], Awaitable[None] | None]

MAX_PATTERN_LENGTH: Final = 200
MAX_NAME_LENGTH: Final = 100

_SPLIT = re.compile(r"\{([^{}]*)\}")
_LITERAL = re.compile(r"[A-Za-z0-9_.:-]*")
_SEPARATOR = re.compile(r"[.:]")
_PLACEHOLDER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SEGMENT = "[A-Za-z0-9_-]+"
_NAME = re.compile(rf"[A-Za-z0-9_.:-]{{1,{MAX_NAME_LENGTH}}}")
_RESERVED_PREFIX = "next:"
_NO_PARAMS: Final[Mapping[str, str]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Upstream:
    """One client event on a granted topic, with the connection that sent it.

    `subject` is the opaque `Handshake.subject`, never the credential behind it.
    """

    topic: str
    name: str
    data: object
    user: object
    subject: str
    connection: str
    params: Mapping[str, str] = _NO_PARAMS


def compile_pattern(pattern: object) -> re.Pattern[str] | None:
    """Return the regex a topic pattern matches with, None for an invalid pattern.

    A placeholder matches one segment, so a colon or a dot has to follow it.
    """
    if (
        not isinstance(pattern, str)
        or not 0 < len(pattern) <= MAX_PATTERN_LENGTH
        or pattern.startswith(_RESERVED_PREFIX)
    ):
        return None
    parts = _SPLIT.split(pattern)
    literals, names = parts[::2], parts[1::2]
    if not all(_LITERAL.fullmatch(literal) for literal in literals):
        return None
    if not all(_PLACEHOLDER.fullmatch(name) for name in names):
        return None
    if len(set(names)) != len(names):
        return None
    if not all(_SEPARATOR.search(literal) for literal in literals[1:-1]):
        return None
    regex = "".join(
        re.escape(literal) if index % 2 == 0 else f"(?P<{literal}>{_SEGMENT})"
        for index, literal in enumerate(parts)
    )
    return re.compile(regex)


def valid_name(name: object) -> bool:
    """Whether an event name is 1 to `MAX_NAME_LENGTH` topic characters."""
    return isinstance(name, str) and _NAME.fullmatch(name) is not None


def handler_workers() -> int:
    """Return how many threads the handler pool runs, sized like Django's default."""
    return min(32, (os.cpu_count() or 1) + 4)


@functools.cache
def handler_pool() -> ThreadPoolExecutor:
    """Return the pool sync handlers run on, apart from the one Django shares.

    A slow handler then fills only its own threads, sized like the default pool.
    """
    return ThreadPoolExecutor(handler_workers(), thread_name_prefix="next-handler")


@functools.cache
def handler_threads() -> threading.BoundedSemaphore:
    """Return the process-wide permits for handler pool threads, one per worker.

    A permit stays taken until its handler returns, past a timeout too.
    """
    return threading.BoundedSemaphore(handler_workers())


after_fork(handler_pool.cache_clear)
after_fork(handler_threads.cache_clear)

_LEASE: ContextVar["Lease | None"] = ContextVar("next_realtime_lease", default=None)
_RUNNING: set[asyncio.Task[Awaitable[None] | None]] = set()


class PoolFullError(Exception):
    """Raised when no handler pool thread is free for a sync handler, never queued."""


class ThreadShare:
    """The handler pool threads one connection holds, half the pool by default."""

    __slots__ = ("busy", "limit")

    def __init__(self, limit: int | None = None) -> None:
        """Start with no thread busy, the limit leaving the rest to other clients."""
        self.limit = max(1, handler_workers() // 2) if limit is None else limit
        self.busy = 0


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

    __slots__ = ("_lease", "_permit")

    def __init__(self, permit: threading.BoundedSemaphore, lease: Lease | None) -> None:
        """Hold a taken permit and the lease it counts against, if any."""
        self._permit = permit
        self._lease = lease

    @classmethod
    def claim(cls) -> Self:
        """Take a free pool thread under the current lease, else `PoolFullError`."""
        permit = handler_threads()
        if not permit.acquire(blocking=False):
            raise PoolFullError
        lease = _LEASE.get()
        if lease is not None and not lease.take():
            permit.release()
            raise PoolFullError
        return cls(permit, lease)

    def give(self) -> None:
        """Return the thread to the pool and to the lease."""
        self._permit.release()
        if self._lease is not None:
            self._lease.give()

    async def run(self, handler: Handler, upstream: Upstream) -> None:
        """Run a sync handler on the thread, a cancel leaving it to finish alone.

        A sync-looking handler that returns an awaitable is awaited, so `ok` means done.
        """
        task = asyncio.create_task(run_pooled(handler_pool(), handler, upstream))
        _RUNNING.add(task)
        task.add_done_callback(self._returned)
        outcome = await asyncio.shield(task)
        if inspect.isawaitable(outcome):
            await outcome

    def _returned(self, task: asyncio.Task[Awaitable[None] | None]) -> None:
        """Give the thread back once the handler returned and retrieve its outcome."""
        _RUNNING.discard(task)
        self.give()
        with suppress(asyncio.CancelledError):
            task.exception()


def _pooled(handler: Handler) -> bool:
    """Whether a handler is sync, so it runs on a handler pool thread."""
    return not (
        iscoroutinefunction(handler) or iscoroutinefunction(type(handler).__call__)
    )


@dataclass(frozen=True, slots=True)
class Route:
    """One handler bound to a topic pattern and an event name."""

    pattern: str
    name: str
    handler: Handler
    matcher: re.Pattern[str] | None
    pooled: bool

    @classmethod
    def bind(cls, pattern: str, name: str, handler: Handler) -> "Route":
        """Bind a handler, with no matcher when the pattern or the name is invalid."""
        matcher = compile_pattern(pattern) if valid_name(name) else None
        return cls(pattern, name, handler, matcher, _pooled(handler))

    @property
    def valid(self) -> bool:
        """Whether the pattern and the name both parse, so the route can match."""
        return self.matcher is not None

    @property
    def placeholders(self) -> int:
        """How many placeholders the pattern holds, fewer is more specific."""
        return 0 if self.matcher is None else self.matcher.groups

    def match(self, topic: str, name: str) -> Mapping[str, str] | None:
        """Return the placeholder values for a topic and name it serves, else None."""
        if self.matcher is None or name != self.name:
            return None
        found = self.matcher.fullmatch(topic)
        if found is None:
            return None
        if not self.matcher.groups:
            return _NO_PARAMS
        return MappingProxyType(found.groupdict())

    def claim(self) -> PoolThread | None:
        """Claim a pool thread for a sync handler, None for an async one."""
        return PoolThread.claim() if self.pooled else None

    async def __call__(
        self, upstream: Upstream, thread: PoolThread | None = None
    ) -> None:
        """Run the handler, a sync one on `thread` or on a thread it claims."""
        if not self.pooled:
            await cast("Callable[[Upstream], Awaitable[None]]", self.handler)(upstream)
            return
        await (thread or PoolThread.claim()).run(self.handler, upstream)


def _origin(handler: Handler) -> tuple[str, int, str] | None:
    """Return where the function a handler wraps is defined, None for no stable site.

    A closure answers None, since one shared wrapper code serves many handlers.
    """
    try:
        defined = inspect.unwrap(handler)
    except ValueError:
        return None
    code = getattr(defined, "__code__", None)
    if not isinstance(code, CodeType) or code.co_freevars:
        return None
    return code.co_filename, code.co_firstlineno, code.co_qualname


def _redefined(held: Handler, handler: Handler) -> bool:
    """Whether `handler` is `held` defined again, by the same file and name."""
    origin = _origin(held)
    return origin is not None and origin == _origin(handler)


class HandlerRegistry:
    """Hold one route per topic pattern and event name, in registration order.

    A second handler under a taken pair is kept aside for the duplicate check.
    """

    def __init__(self) -> None:
        """Start empty."""
        self._routes: list[Route] = []
        self._index: dict[tuple[str, str], Route] = {}
        self._by_name: dict[str, list[Route]] = {}
        self._duplicates: dict[tuple[str, str], Route] = {}
        self._version = 0

    @property
    def version(self) -> int:
        """Monotonic counter bumped on every change, a token for derived memos."""
        return self._version

    def add(self, entry: Route) -> Route:
        """Register a route, the same function defined again replacing its pair."""
        key = (entry.pattern, entry.name)
        held = self._index.get(key)
        named = self._by_name.setdefault(entry.name, [])
        if held is None:
            self._routes.append(entry)
            named.append(entry)
        elif held.handler is entry.handler or _redefined(held.handler, entry.handler):
            self._routes[self._routes.index(held)] = entry
            named[named.index(held)] = entry
        else:
            self._duplicates[key] = entry
            return held
        self._index[key] = entry
        self._version += 1
        return entry

    def resolve(self, topic: str, name: str) -> tuple[Route, Mapping[str, str]] | None:
        """Return the route serving an event and its placeholder values, or None.

        The pattern with the fewest placeholders wins, then the one registered first.
        """
        best: tuple[Route, Mapping[str, str]] | None = None
        for route in self._by_name.get(name, ()):
            params = route.match(topic, name)
            if params is None:
                continue
            if best is None or route.placeholders < best[0].placeholders:
                best = (route, params)
        return best

    def duplicates(self) -> tuple[tuple[Route, Route], ...]:
        """Return each held route with the other handler claiming its pair."""
        duplicates = self._duplicates.items()
        return tuple((self._index[key], other) for key, other in duplicates)

    def __iter__(self) -> Iterator[Route]:
        """Yield every route in registration order."""
        return iter(tuple(self._routes))

    def __len__(self) -> int:
        """Return how many routes are registered."""
        return len(self._routes)

    def reset(self) -> None:
        """Drop every route and every recorded duplicate."""
        self._routes.clear()
        self._index.clear()
        self._by_name.clear()
        self._duplicates.clear()
        self._version += 1


handler_registry = HandlerRegistry()


def on[H: Handler](pattern: str, name: str) -> Callable[[H], H]:
    """Register a sync or async function for the `name` events on matching topics.

    `{placeholder}` segments in `pattern` reach the handler as `Upstream.params`.
    """

    def decorator(handler: H) -> H:
        handler_registry.add(Route.bind(pattern, name, handler))
        return handler

    return decorator


async def dispatch(upstream: Upstream) -> bool:
    """Run the handler of one upstream event, False when no handler serves it.

    A sync handler claims its thread before `upstream_received`, else `PoolFullError`.
    """
    found = handler_registry.resolve(upstream.topic, upstream.name)
    if found is None:
        return False
    bound, params = found
    if params:
        upstream = replace(upstream, params=params)
    thread = bound.claim()
    try:
        await areceived(bound.handler, upstream)
    except BaseException:
        if thread is not None:
            thread.give()
        raise
    await bound(upstream, thread)
    return True


def handler_modules() -> Sequence[str] | None:
    """Return the modules `REALTIME['HANDLERS']` lists, None to discover every app's.

    A value that is no list of module names imports nothing and fails its check.
    """
    modules = scope_value("REALTIME", "HANDLERS")
    if modules is None:
        return None
    if valid_modules(modules):
        return tuple(modules)
    return ()


def valid_modules(modules: object) -> TypeGuard[Sequence[str]]:
    """Whether a `HANDLERS` value is a list or a tuple of dotted module names."""
    return isinstance(modules, list | tuple) and all(
        isinstance(module, str) and all(map(str.isidentifier, module.split(".")))
        for module in modules
    )


def autodiscover_handlers() -> None:
    """Import the handler modules, the `realtime` module of every app by default."""
    modules = handler_modules()
    if modules is None:
        autodiscover_modules("realtime")
        return
    for module in modules:
        import_module(module)


__all__ = [
    "MAX_NAME_LENGTH",
    "MAX_PATTERN_LENGTH",
    "Handler",
    "HandlerRegistry",
    "Lease",
    "PoolFullError",
    "PoolThread",
    "Route",
    "ThreadShare",
    "Upstream",
    "autodiscover_handlers",
    "compile_pattern",
    "dispatch",
    "handler_modules",
    "handler_pool",
    "handler_registry",
    "handler_threads",
    "handler_workers",
    "on",
    "valid_modules",
    "valid_name",
]
