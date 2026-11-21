"""What the view reads on the request thread for placement and replay on the pool."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from functools import partial
from typing import NamedTuple

from django.db import connections, transaction
from django.db.backends.sqlite3.base import DatabaseWrapper as SQLiteWrapper
from django.http import HttpRequest

from next.suspense.drivers import server_loop
from next.suspense.markers import Captured, ForegroundFacts, Server
from next.suspense.transport import TransportFacts, transport_of
from next.transactions import probe_atomic_aliases

from .backends import seams
from .config import parallel, sqlite_parallel
from .detection import connection_sources, thread_sources


class States(NamedTuple):
    """The thread and connection states one response captured, replayed per job."""

    threads: tuple[Captured, ...] = ()
    connections: tuple[Captured, ...] = ()


_forced: ContextVar[bool] = ContextVar("next_pool_forced", default=False)


@contextmanager
def forcing_pool() -> Iterator[None]:
    """Let the jobs inside the block take the pool despite an atomic block or SQLite."""
    token = _forced.set(True)
    try:
        yield
    finally:
        _forced.reset(token)


def pool_forced() -> bool:
    """Whether `forcing_pool` holds in this context."""
    return _forced.get()


def server_of(
    request: HttpRequest | None, transport: TransportFacts | None = None
) -> Server:
    """Return the server kind, ASGI when a running loop's `sync_to_async` runs us.

    A non-concurrent transport makes a sync worker, and no request a threaded one.
    """
    if server_loop() is not None:
        return Server.ASGI
    if request is None:
        return Server.WSGI_THREADED
    held = transport_of(request) if transport is None else transport
    return Server.WSGI_THREADED if held.concurrent else Server.WSGI_SYNC


def _sqlite() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the SQLite aliases and the in-memory ones among them, opening nothing."""
    sqlite: list[str] = []
    memory: list[str] = []
    for alias in connections:
        connection = connections[alias]
        if connection.vendor != "sqlite":
            continue
        sqlite.append(alias)
        if isinstance(connection, SQLiteWrapper) and connection.is_in_memory_db():
            memory.append(alias)
    return tuple(sqlite), tuple(memory)


def foreground_facts(request: HttpRequest | None) -> ForegroundFacts:
    """Return the facts the request-lane rules read, on the thread that ran the view.

    Under `forcing_pool` neither an atomic block nor a SQLite alias is reported.
    """
    forced = _forced.get()
    sqlite, memory = ((), ()) if forced else _sqlite()
    return ForegroundFacts(
        server=server_of(request),
        atomic=() if forced else probe_atomic_aliases(),
        sqlite=sqlite,
        memory=memory,
        sqlite_parallel=sqlite_parallel(),
        connection_sources=connection_sources(),
        thread_sources=thread_sources(),
        parallel=parallel(),
    )


class Outlived:
    """The atomic aliases a view read, each struck off once its transaction commits.

    A stream runs its jobs after the view, so a block it closed by then pins none.
    """

    __slots__ = ("_open",)

    def __init__(self, aliases: tuple[str, ...]) -> None:
        """Watch the outermost transaction of each alias for its commit."""
        self._open = dict.fromkeys(aliases)
        for alias in aliases:
            transaction.on_commit(partial(self._open.pop, alias, None), using=alias)

    def facts(self, facts: ForegroundFacts) -> ForegroundFacts:
        """Return `facts` naming only the atomic aliases whose block is still open."""
        if len(self._open) == len(facts.atomic):
            return facts
        return replace(facts, atomic=tuple(self._open))


def capture_states(request: HttpRequest | None) -> States:
    """Run every backend's `capture` in the view, an alias at a time for connections."""
    if request is None:
        return States()
    held = seams()
    threads = tuple(
        Captured(backend, state)
        for backend in held.threads
        if (state := backend.capture(request)) is not None
    )
    captured = tuple(
        Captured(backend, state, alias)
        for alias in connections
        for backend in held.connections
        if (state := backend.capture(request, alias)) is not None
    )
    return States(threads, captured)


__all__ = [
    "Outlived",
    "States",
    "capture_states",
    "forcing_pool",
    "foreground_facts",
    "pool_forced",
    "server_of",
]
