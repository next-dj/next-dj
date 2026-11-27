import threading
from typing import TYPE_CHECKING

from django.db.utils import ConnectionHandler

from next.bridge.tasks import StealableTask
from tests.support.waits import WAIT


if TYPE_CHECKING:
    from collections.abc import Callable


def pool_threads() -> set[threading.Thread]:
    return {
        thread
        for thread in threading.enumerate()
        if thread.name.startswith("next-pool-")
    }


def blocked(gate: threading.Event) -> StealableTask[bool]:
    return StealableTask(lambda: gate.wait(WAIT))


class Clock:
    """Stand-in for the `time` module of the pool, its monotonic clock moved by hand."""

    def __init__(self) -> None:
        """Start the clock at a fixed instant."""
        self.now = 100.0

    def monotonic(self) -> float:
        return self.now


class Connection:
    """Stand-in for a Django connection, with the fields the pool hooks read."""

    def __init__(self, alias: str = "default") -> None:
        """Open a connection on `alias`, outside any transaction."""
        self.alias = alias
        self.connection: object | None = object()
        self.in_atomic_block = False
        self.refreshed = 0
        self.closed = threading.Event()
        self.execute_wrappers: list[Callable[..., object]] = []

    def close_if_unusable_or_obsolete(self) -> None:
        self.refreshed += 1

    def close(self) -> None:
        self.connection = None
        self.closed.set()


class Connections:
    """Stand-in for `django.db.connections`, holding connections per thread."""

    def __init__(self) -> None:
        """Hold no connection on any thread yet."""
        self._held = threading.local()

    def hold(self, *held: Connection) -> None:
        self._held.connections = list(held)

    def all(self, *, initialized_only: bool = False) -> list[Connection]:
        return getattr(self._held, "connections", [])


def select(handler: ConnectionHandler) -> object:
    """Run one query on the default alias and return the raw connection it used."""
    conn = handler["default"]
    with conn.cursor() as cursor:
        cursor.execute("SELECT 1")
    return conn.connection


def raw(handler: ConnectionHandler) -> object:
    return handler["default"].connection
