"""Replay captured state around a job on a pool thread, connection state lazily."""

import re
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from types import TracebackType
from typing import Final, Protocol, cast

from django.db import connections, transaction
from django.db.backends.base.base import BaseDatabaseWrapper

from next.suspense.executors import retire_thread
from next.suspense.markers import Captured
from next.transactions import StatementKind, statement_kind

from .backends import ConnectionState, ThreadState
from .config import DeadlineMode, deadline_mode, timeout_floor
from .deadline import deadline_statement


_POSTGRESQL: Final = "postgresql"
_HOOK_FIELDS: Final = 3
_SAVEPOINT: Final = re.compile(r"\s*(?:RELEASE|ROLLBACK\s+TO)\b", re.IGNORECASE)

type _Entries = list[tuple[ConnectionState, object]]
type _Statement = Callable[[], tuple[str, tuple[str, str]]]


class Replayable(Protocol):
    """What a job replays, a `BoundarySession` or the `States` a capture returned."""

    @property
    def threads(self) -> tuple[Captured, ...]:
        """Return the captured thread states in backend order."""

    @property
    def connections(self) -> tuple[Captured, ...]:
        """Return the captured connection states, each naming its alias."""


class _Hook:
    """The commit hook whose wait proves that the replayed transaction is still open."""

    __slots__ = ()

    def __call__(self) -> None:
        """Do nothing, as only its place among the commit hooks counts."""


def _pending(connection: BaseDatabaseWrapper, hook: _Hook | None) -> bool | None:
    """Whether `hook` waits among the commit hooks, `None` when they read differently.

    Django drops them on commit and on rollback, and a savepoint rollback drops the
    ones it covers, just as the server undoes a transaction-local setting.
    """
    hooks = getattr(connection, "run_on_commit", None)
    if not isinstance(hooks, list):
        return None
    for entry in hooks:
        if not (isinstance(entry, tuple) and len(entry) == _HOOK_FIELDS):
            return None
        if entry[1] is hook:
            return True
    return False


def _savepoint(sql: object) -> bool:
    """Whether a control statement only releases or rolls back to a savepoint."""
    return isinstance(sql, str) and _SAVEPOINT.match(sql) is not None


class _Replay:
    """The execute wrapper replaying one alias's state before its first data statement.

    Applied in a user's block, it holds while its commit hook still waits, and a
    commit hook list it cannot read replays before every data statement instead.
    """

    __slots__ = (
        "block",
        "borrowed",
        "busy",
        "connection",
        "hook",
        "stale",
        "states",
        "timeout",
    )

    def __init__(
        self,
        connection: BaseDatabaseWrapper,
        states: _Entries,
        timeout: _Statement | None,
    ) -> None:
        """Hold the states and the deadline statement, applying nothing yet."""
        self.connection = connection
        self.states = tuple(states)
        self.timeout = timeout
        self.block: transaction.Atomic | None = None
        self.borrowed = False
        self.stale = False
        self.busy = False
        self.hook: _Hook | None = None

    def __call__(
        self, execute: Callable[..., object], sql: object, *rest: object
    ) -> object:
        """Replay the state when the statement reads or writes data without it."""
        if self.busy or self.block is not None:
            return execute(sql, *rest)
        kind = statement_kind(sql)
        in_block = self.connection.in_atomic_block
        if kind < StatementKind.READ:
            control = kind is StatementKind.CONTROL and not _savepoint(sql)
            if self.borrowed and (control or not in_block):
                self.stale = True
            return execute(sql, *rest)
        if self.borrowed and in_block and self._holds():
            return execute(sql, *rest)
        self.busy = True
        try:
            opened = self._apply()
        finally:
            self.busy = False
        if opened is None:
            return execute(sql, *rest)
        try:
            return execute(sql, *rest)
        except BaseException as exc:
            self._abandon(opened, exc)
            raise

    def _holds(self) -> bool:
        """Whether the state replayed in the user's block holds in its transaction."""
        if self.stale or self.hook is None:
            return False
        return _pending(self.connection, self.hook) is True

    def _apply(self) -> transaction.Atomic | None:
        """Replay in the open block, else open the state transaction, replay, return it.

        A doomed block gets nothing, its statement raising before it reaches the server.
        """
        connection = self.connection
        alias = connection.alias
        if connection.in_atomic_block:
            if not transaction.get_rollback(using=alias):
                self._replay()
                self.borrowed = True
                self.stale = False
                self.hook = None
                if _pending(connection, None) is not None:
                    self.hook = _Hook()
                    transaction.on_commit(self.hook, using=alias)
            return None
        block = transaction.atomic(using=alias)
        block.__enter__()
        self.block = block
        self.borrowed = False
        self.stale = False
        try:
            self._replay()
        except BaseException as exc:
            self._abandon(block, exc)
            raise
        return block

    def _replay(self) -> None:
        connection = self.connection
        if self.timeout is not None:
            sql, params = self.timeout()
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
        for backend, state in self.states:
            backend.apply(state, connection)

    def _abandon(self, block: transaction.Atomic, exc: BaseException) -> None:
        """Roll back a state transaction whose first statement failed, like autocommit.

        The next data statement opens a fresh one and replays the state again.
        """
        self.block = None
        block.__exit__(type(exc), exc, exc.__traceback__)

    def close(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """Commit the state transaction on success and roll it back on an exception."""
        block = self.block
        self.block = None
        if block is not None:
            block.__exit__(exc_type, exc_value, traceback)
        return False


def _remaining(deadline: float, floor: float) -> _Statement:
    def statement() -> tuple[str, tuple[str, str]]:
        return deadline_statement(deadline - time.monotonic(), floor)

    return statement


def _entries(captured: tuple[Captured, ...], mode: DeadlineMode) -> dict[str, _Entries]:
    """Group the captured states by alias, adding PostgreSQL aliases under `always`."""
    entries: dict[str, _Entries] = {}
    for item in captured:
        if item.alias is not None:
            source = cast("ConnectionState", item.source)
            entries.setdefault(item.alias, []).append((source, item.state))
    if mode == "always":
        for alias in connections:
            if connections[alias].vendor == _POSTGRESQL:
                entries.setdefault(alias, [])
    return entries


def _apply_threads(stack: ExitStack, captured: tuple[Captured, ...]) -> None:
    """Apply each thread state, queueing its reset so they unwind in reverse order.

    A state that fails to apply or reset may linger, so the pool retires the thread.
    """
    for item in captured:
        backend = cast("ThreadState", item.source)
        try:
            token = backend.apply(item.state)
        except BaseException:
            retire_thread()
            raise
        stack.callback(_reset, backend, token)


def _reset(backend: ThreadState, token: object) -> None:
    try:
        backend.reset(token)
    except BaseException:
        retire_thread()
        raise


def _wrap(
    stack: ExitStack, entries: dict[str, _Entries], mode: DeadlineMode, deadline: float
) -> None:
    """Push one replay wrapper per alias, its connection opened by the first query."""
    floor = timeout_floor()
    for alias, states in entries.items():
        connection = connections[alias]
        timeout = None
        if mode is not False and connection.vendor == _POSTGRESQL:
            timeout = _remaining(deadline, floor)
        replay = _Replay(connection, states, timeout)
        stack.enter_context(connection.execute_wrapper(replay))
        stack.push(replay.close)


@contextmanager
def job_guard(states: Replayable, *, deadline: float) -> Iterator[None]:
    """Run a pool job with the view's thread state and lazy connection state replayed.

    `deadline` is the job's monotonic settle instant, and every replay unwinds on exit.
    Its no-op commit hook shows among the callbacks `captureOnCommitCallbacks` collects.
    """
    mode = deadline_mode()
    entries = _entries(states.connections, mode)
    if not states.threads and not entries:
        yield
        return
    with ExitStack() as stack:
        _apply_threads(stack, states.threads)
        _wrap(stack, entries, mode, deadline)
        yield


__all__ = ["Replayable", "job_guard"]
