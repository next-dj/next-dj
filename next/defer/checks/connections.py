"""The checks of the connections deferred jobs run on, their sources and pools."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Final

from django.conf import settings
from django.core.checks import CheckMessage, Warning as DjangoWarning

from next.checks import NEXT, register
from next.conf.defer import defer_count, defer_seconds, parallel
from next.defer.connections.detection import connection_sources, thread_sources


_POOLED: Final = frozenset(
    {"django.db.backends.postgresql", "django.contrib.gis.db.backends.postgis"}
)
_MIN_SIZE: Final = 4
"""The `min_size` psycopg_pool gives a pool, its `max_size` defaulting to it."""

_FRESH: Final = (
    " With CONN_MAX_AGE 0 each boundary a pool thread runs opens a new connection."
)

_TIMEOUT: Final = 30.0
"""The seconds psycopg_pool waits for a free connection by default."""


@dataclass(frozen=True, slots=True)
class Pool:
    """The connection pool of one alias, its size and wait as the settings give them.

    Either is `None` when the settings hold a value psycopg_pool would refuse.
    """

    alias: str
    size: int | None
    timeout: float | None


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _seconds(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def pools() -> Iterator[Pool]:
    """Yield the pool of each PostgreSQL alias with `OPTIONS["pool"]`, read statically.

    `True` takes the psycopg_pool defaults, so psycopg itself is never imported.
    """
    for alias, config in settings.DATABASES.items():
        if not isinstance(config, Mapping) or config.get("ENGINE") not in _POOLED:
            continue
        options = config.get("OPTIONS")
        pool = options.get("pool") if isinstance(options, Mapping) else None
        if pool is True:
            pool = {}
        if not isinstance(pool, Mapping):
            continue
        least = _count(pool.get("min_size", _MIN_SIZE))
        most = pool.get("max_size")
        size = least if most is None else _count(most)
        yield Pool(str(alias), size, _seconds(pool.get("timeout", _TIMEOUT)))


@register(NEXT)
def check_connection_sources(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about per-connection state no `ConnectionState` backend covers."""
    return [
        DjangoWarning(
            f"Per-connection state from {source!r} has no ConnectionState backend, "
            "so every deferred job runs on the request thread.",
            hint=(
                "List a ConnectionState backend whose covers names it in "
                "NEXT_FRAMEWORK['DEFER']['CONNECTION_STATE'], or set "
                "next_stateless = True on a router that keeps no state."
            ),
            obj=settings,
            id="next.W155",
        )
        for source in connection_sources()
    ]


@register(NEXT)
def check_thread_sources(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about thread-local scoping no `ThreadState` backend covers."""
    return [
        DjangoWarning(
            f"Thread-local scoping from {source!r} has no ThreadState backend, "
            "so every deferred job runs on the request thread.",
            hint=(
                "List a ThreadState backend whose covers names it in "
                "NEXT_FRAMEWORK['DEFER']['THREAD_STATE']."
            ),
            obj=settings,
            id="next.W171",
        )
        for source in thread_sources()
    ]


@register(NEXT)
def check_pool_budget(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about a pool smaller than the connections one process may hold.

    Each request a process serves at once holds one, and so does each pool worker.
    """
    workers = defer_count("EXECUTOR", "OPTIONS", "MAX_WORKERS")
    budget = defer_count("REQUEST_CONCURRENCY") + workers
    return [
        DjangoWarning(
            f"The connection pool of the database alias {pool.alias!r} holds at "
            f"most {pool.size} connections, below the {budget} that the requests "
            "one process serves at once, REQUEST_CONCURRENCY, and its pool workers, "
            "EXECUTOR['OPTIONS']['MAX_WORKERS'] of NEXT_FRAMEWORK['DEFER'], hold, so "
            "a deferred job waits for a connection another thread holds.",
            hint=(
                f"Raise OPTIONS['pool']['max_size'] to at least {budget}, or lower "
                "MAX_WORKERS. REQUEST_CONCURRENCY counts the threads of a WSGI "
                "worker, or the concurrency limit of an ASGI one."
            ),
            obj=settings,
            id="next.W162",
        )
        for pool in pools()
        if pool.size is not None and pool.size < budget
    ]


def unpooled() -> Iterator[tuple[str, Mapping[str, object]]]:
    """Yield each PostgreSQL alias without `OPTIONS["pool"]` with its settings."""
    for alias, config in settings.DATABASES.items():
        if not isinstance(config, Mapping) or config.get("ENGINE") not in _POOLED:
            continue
        options = config.get("OPTIONS")
        if isinstance(options, Mapping) and options.get("pool"):
            continue
        yield str(alias), config


@register(NEXT, deploy=True)
def check_connection_budget(*args: object, **kwargs: object) -> list[CheckMessage]:
    """State the connections one process may open on each unpooled PostgreSQL alias.

    Pool threads start on demand, so they open their connections at the peak of load.
    """
    if not parallel():
        return []
    requests = defer_count("REQUEST_CONCURRENCY")
    workers = defer_count("EXECUTOR", "OPTIONS", "MAX_WORKERS")
    budget = requests + workers
    messages: list[CheckMessage] = []
    for alias, config in unpooled():
        fresh = _FRESH if config.get("CONN_MAX_AGE", 0) == 0 else ""
        messages.append(
            DjangoWarning(
                f"The database alias {alias!r} may hold up to {budget} connections "
                f"per process, REQUEST_CONCURRENCY {requests} plus "
                f"EXECUTOR['OPTIONS']['MAX_WORKERS'] {workers} of "
                "NEXT_FRAMEWORK['DEFER'], as each pool thread opens its own at the "
                "peak of load. Multiply it by the worker processes of every host and "
                f"compare the sum with max_connections of the server.{fresh}",
                hint=(
                    "Put PgBouncer in transaction mode in front of the server, set "
                    "OPTIONS['pool'] of the alias, or lower MAX_WORKERS, then "
                    "silence next.W189 once the sum fits."
                ),
                obj=settings,
                id="next.W189",
            )
        )
    return messages


@register(NEXT)
def check_pool_timeout(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about a pool that waits for a connection past the response deadline."""
    deadline = defer_seconds("DEADLINE")
    return [
        DjangoWarning(
            f"The connection pool of the database alias {pool.alias!r} waits up to "
            f"{pool.timeout:g} seconds for a connection, past the "
            f"NEXT_FRAMEWORK['DEFER']['DEADLINE'] of {deadline:g} seconds, so a "
            "deferred job blocked on the pool holds its thread after its boundary "
            "settled as a timeout.",
            hint=(
                "Set OPTIONS['pool']['timeout'] of the alias at or below the deadline."
            ),
            obj=settings,
            id="next.W172",
        )
        for pool in pools()
        if pool.timeout is not None and pool.timeout > deadline
    ]


__all__ = [
    "Pool",
    "check_connection_budget",
    "check_connection_sources",
    "check_pool_budget",
    "check_pool_timeout",
    "check_thread_sources",
    "pools",
    "unpooled",
]
