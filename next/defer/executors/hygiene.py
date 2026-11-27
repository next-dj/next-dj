"""The care a pool thread gives the Django connections a task opened and left behind."""

import logging
from collections.abc import Callable
from contextlib import suppress

from django.db import DatabaseError, InterfaceError, connections
from django.db.backends.base.base import BaseDatabaseWrapper


logger = logging.getLogger("next.defer")


type Wrappers = dict[str, list[Callable[..., object]]]


def wrappers() -> Wrappers:
    """Return the execute wrappers of every connection this thread has opened."""
    return {
        conn.alias: list(conn.execute_wrappers)
        for conn in connections.all(initialized_only=True)
    }


def restore_wrappers(saved: Wrappers) -> None:
    """Put back the execute wrappers each connection had before a task, none if new."""
    for conn in connections.all(initialized_only=True):
        conn.execute_wrappers = list(saved.get(conn.alias, ()))


def close(conn: BaseDatabaseWrapper) -> None:
    """Close one connection, a failing close leaving it closed all the same."""
    with suppress(DatabaseError, InterfaceError):
        conn.close()


def close_connections() -> None:
    """Close every connection this thread opened, as an idle worker holds none."""
    for conn in connections.all(initialized_only=True):
        close(conn)


def refresh(conn: BaseDatabaseWrapper) -> None:
    """Close `conn` when broken or past `CONN_MAX_AGE`, rearming its health check.

    It is the check Django runs on every connection as a request starts and finishes.
    """
    with suppress(DatabaseError, InterfaceError):
        conn.close_if_unusable_or_obsolete()


def refresh_connections() -> None:
    """Run the request-boundary check on every connection this thread opened."""
    for conn in connections.all(initialized_only=True):
        refresh(conn)


def release_connections(discarded: set[str]) -> None:
    """Discard a connection a task left in an atomic block, check the others.

    `discarded` holds the aliases already warned of, each warned of once per pool.
    """
    for conn in connections.all(initialized_only=True):
        if conn.in_atomic_block:
            _discard(conn, discarded)
        else:
            refresh(conn)


def _discard(conn: BaseDatabaseWrapper, discarded: set[str]) -> None:
    """Close `conn`, which ends its transaction, so no next task inherits it.

    No public call exits a block whose `Atomic` the task dropped, so the flag goes.
    """
    if conn.alias not in discarded:
        discarded.add(conn.alias)
        logger.warning(
            "A pool task left the %r connection inside an atomic block, so the "
            "pool closed it, which rolls its transaction back.",
            conn.alias,
        )
    conn.in_atomic_block = False
    conn.closed_in_transaction = False
    close(conn)


__all__ = [
    "Wrappers",
    "close",
    "close_connections",
    "refresh",
    "refresh_connections",
    "release_connections",
    "restore_wrappers",
    "wrappers",
]
