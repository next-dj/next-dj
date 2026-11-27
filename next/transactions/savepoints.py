"""Lazy savepoints and view transactions that wait for the first statement they need."""

import threading
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager, ExitStack
from types import TracebackType
from typing import Final, Literal, Self, override

from django.db import Error, connections, transaction
from django.db.backends.base.base import BaseDatabaseWrapper

from .statements import StatementKind, nests, statement_kind


WRITES: Final = "writes"
"""The `atomic` value that opens a transaction before the first write or commit hook."""

type AtomicMode = bool | Literal["writes"]

_NEVER: Final = StatementKind.WRITE + 1


def _opening_kind(atomic: AtomicMode) -> int:
    """Return the least statement kind that opens a transaction in autocommit."""
    if atomic is True:
        return StatementKind.READ
    if atomic is False:
        return _NEVER
    return StatementKind.WRITE


class _Frame:
    """One protected subtree on one connection, and what it took there.

    `broken` marks a subtree whose transaction failed before it took a savepoint.
    """

    __slots__ = ("acted", "block", "broken", "inside", "opens", "sid", "view")

    def __init__(self, opens: int, *, inside: bool, view: bool) -> None:
        """Record the autocommit choice, an open transaction and a view's own frame."""
        self.opens = opens
        self.inside = inside
        self.view = view
        self.acted = False
        self.broken = False
        self.sid: str | None = None
        self.block: transaction.Atomic | None = None

    @property
    def holds(self) -> bool:
        """Whether the subtree took a savepoint or a transaction it can roll back to."""
        return self.sid is not None or self.block is not None


type _Opening = tuple[transaction.Atomic, list[_Frame]]


class _Guard:
    """The execute wrapper one connection carries while lazy savepoints are open on it.

    Inside a transaction only the outermost subtree takes its savepoint before a read.
    """

    __slots__ = ("busy", "connection", "frames", "pending")

    def __init__(self, connection: BaseDatabaseWrapper) -> None:
        """Start with no subtree to watch."""
        self.connection = connection
        self.frames: list[_Frame] = []
        self.pending = 0
        self.busy = False

    def __call__(
        self, execute: Callable[..., object], sql: object, *rest: object
    ) -> object:
        """Act for the waiting subtrees before the statement runs."""
        if not self.pending or self.busy:
            return execute(sql, *rest)
        kind = statement_kind(sql)
        if kind <= StatementKind.STANDALONE:
            return execute(sql, *rest)
        self.busy = True
        try:
            opening = self._act(kind, sql)
        finally:
            self.busy = False
        if opening is None:
            try:
                return execute(sql, *rest)
            except Error:
                self._broke()
                raise
        try:
            result = execute(sql, *rest)
        except BaseException as exc:
            self._abandon(opening, exc)
            raise
        return result

    def on_commit(self, func: Callable[[], object], *flags: bool) -> None:
        """Take what the waiting subtrees need, as a write would, then register `func`.

        A hook set before the first write then waits for the commit of what follows it.
        `flags` carries the `robust` flag `transaction.on_commit` passes positionally.
        """
        if self.pending and not self.busy:
            self.busy = True
            try:
                self._act(StatementKind.WRITE, None)
            finally:
                self.busy = False
        connection = self.connection
        type(connection).on_commit(connection, func, *flags)

    def push(self, frame: _Frame) -> None:
        """Start watching one more subtree, the innermost so far."""
        self.frames.append(frame)
        self.pending += 1

    @property
    def awaits(self) -> bool:
        """Whether a view transaction waits here in autocommit for its first write."""
        return any(
            frame.view and not frame.acted and not frame.inside for frame in self.frames
        )

    def _act(self, kind: StatementKind, sql: object) -> _Opening | None:
        """Take what the waiting subtrees need, returning the transaction opened now.

        A read needs one savepoint, a write or a foreign savepoint one per subtree.
        """
        connection = self.connection
        waiting = [frame for frame in self.frames if not frame.acted]
        if connection.in_atomic_block:
            if not transaction.get_rollback(using=connection.alias):
                each = kind is StatementKind.WRITE or nests(kind, sql)
                held = any(frame.holds for frame in self.frames)
                for frame in waiting:
                    if frame.inside and (each or not held):
                        self._savepoint(frame)
                        held = True
            return None
        if all(kind < frame.opens for frame in waiting):
            return None
        block = transaction.atomic(using=connection.alias)
        block.__enter__()
        opening = (block, waiting)
        waiting[0].block = block
        self._took(waiting[0])
        try:
            for frame in waiting[1:]:
                self._savepoint(frame)
        except BaseException as exc:
            self._abandon(opening, exc)
            raise
        return opening

    def _took(self, frame: _Frame) -> None:
        frame.acted = True
        self.pending -= 1

    def _savepoint(self, frame: _Frame) -> None:
        frame.sid = self.connection.savepoint()
        self._took(frame)

    def _broke(self) -> None:
        """Mark subtrees a failed statement left in a transaction they cannot undo."""
        if self.connection.in_atomic_block:
            for frame in self.frames:
                if self._lazy(frame):
                    frame.broken = True

    def _lazy(self, frame: _Frame) -> bool:
        """Whether `frame` waits for a write inside a subtree holding a savepoint."""
        return (
            frame.inside
            and not frame.acted
            and any(other.holds for other in self.frames)
        )

    def _abandon(self, opening: _Opening, exc: BaseException) -> None:
        """Roll back a transaction whose first statement failed, as autocommit would.

        The subtrees that acted for it wait again for their next statement.
        """
        block, frames = opening
        try:
            block.__exit__(type(exc), exc, exc.__traceback__)
        finally:
            for frame in frames:
                if frame.acted:
                    frame.acted = False
                    frame.block = None
                    frame.sid = None
                    self.pending += 1

    def settle(self, frame: _Frame, *, failed: bool) -> None:
        """Keep or undo what the subtree did here, then stop watching it."""
        if self._lazy(frame) and self._doomed():
            frame.broken = True
        try:
            if frame.block is not None:
                frame.block.__exit__(Exception if failed else None, None, None)
            elif frame.sid is not None:
                self._release(frame.sid, failed=failed)
        finally:
            self.frames.remove(frame)
            if not frame.acted:
                self.pending -= 1

    def _doomed(self) -> bool:
        connection = self.connection
        return connection.in_atomic_block and transaction.get_rollback(
            using=connection.alias
        )

    def _release(self, sid: str, *, failed: bool) -> None:
        """Release or roll back a savepoint as Django's atomic block exits its own."""
        connection = self.connection
        if not connection.in_atomic_block:
            return
        alias = connection.alias
        if not failed and not transaction.get_rollback(using=alias):
            try:
                connection.savepoint_commit(sid)
            except Error:
                transaction.set_rollback(True, using=alias)
                raise
            return
        transaction.set_rollback(False, using=alias)
        try:
            connection.savepoint_rollback(sid)
            connection.savepoint_commit(sid)
        except Error:
            transaction.set_rollback(True, using=alias)


class _Watch:
    """The frames one handle pushed, one per connection it covers.

    A subtree that opens no transaction still nests in a view transaction yet to open.
    """

    __slots__ = ("entries", "opens", "view")

    def __init__(self, opens: int, *, view: bool = False) -> None:
        """Start covering no connection."""
        self.opens = opens
        self.view = view
        self.entries: dict[BaseDatabaseWrapper, tuple[_Guard, _Frame]] = {}

    def cover(self, session: "_Session", connection: BaseDatabaseWrapper) -> None:
        """Push a frame for `connection` unless it has one or the subtree needs none."""
        if connection in self.entries:
            return
        inside = connection.in_atomic_block
        if not inside and self.opens == _NEVER and not session.awaits(connection):
            return
        guard = session.guard(connection)
        frame = _Frame(self.opens, inside=inside, view=self.view)
        guard.push(frame)
        self.entries[connection] = (guard, frame)

    @property
    def broken(self) -> bool:
        """Whether a transaction the subtree cannot roll back failed under it."""
        return any(frame.broken for _guard, frame in self.entries.values())

    def settle(self, *, failed: bool) -> None:
        """Keep or undo the subtree's work on each connection, the last one first."""
        entries = tuple(self.entries.values())
        if len(entries) == 1:
            guard, frame = entries[0]
            guard.settle(frame, failed=failed)
            return
        with ExitStack() as stack:
            for guard, frame in entries:
                stack.callback(guard.settle, frame, failed=failed)


class _Session:
    """The guards one thread carries while any lazy savepoint is open on it.

    Nested handles share it, so the wrappers go on and off once per outermost handle.
    """

    __slots__ = ("complete", "guards", "stack", "watches")

    def __init__(self) -> None:
        """Start with no guard installed."""
        self.guards: dict[BaseDatabaseWrapper, _Guard] = {}
        self.watches: list[_Watch] = []
        self.complete = False
        self.stack = ExitStack()

    def guard(self, connection: BaseDatabaseWrapper) -> _Guard:
        """Return the guard of `connection`, installing it on first use."""
        guard = self.guards.get(connection)
        if guard is None:
            guard = self.guards[connection] = _Guard(connection)
            self.stack.enter_context(connection.execute_wrapper(guard))
            shadows = vars(connection)
            shadows["on_commit"] = guard.on_commit
            self.stack.callback(shadows.pop, "on_commit", None)
        return guard

    def awaits(self, connection: BaseDatabaseWrapper) -> bool:
        """Whether a view transaction waits on `connection` for its first write."""
        guard = self.guards.get(connection)
        return guard is not None and guard.awaits

    def guard_every(self) -> None:
        """Install a guard on every connection this thread holds."""
        if not self.complete:
            for connection in connections.all(initialized_only=True):
                self.guard(connection)
            self.complete = True

    def leave(self, watch: _Watch) -> None:
        """Forget one closed handle, removing every guard after the last one.

        A block that keeps the session leaves the guards on for the handles after it.
        """
        self.watches.remove(watch)
        if not self.watches and not _local.kept:
            self.close()

    def close(self) -> None:
        """Take every guard off and stop being the session of this thread."""
        _local.session = None
        self.stack.close()


class _Local(threading.local):
    """The lazy savepoint session of the current thread and the blocks that keep it."""

    session: _Session | None = None
    kept: int = 0


_local = _Local()


class _Kept:
    """The block one render keeps its lazy savepoint session through."""

    __slots__ = ()

    def __enter__(self) -> None:
        """Count one more block that keeps the session of this thread."""
        _local.kept += 1

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the session once no block keeps it and no handle is open in it."""
        _local.kept -= 1
        session = _local.session
        if session is not None and not _local.kept and not session.watches:
            session.close()


_KEPT: Final = _Kept()


def keep_savepoints() -> AbstractContextManager[None]:
    """Keep the guards of the lazy savepoints on between the handles of one block.

    Sibling subtrees then share one session, so each handle after the first pushes a
    frame and installs nothing. It opens no session, the first handle inside does.
    """
    return _KEPT


def cover_new_connection(*, connection: BaseDatabaseWrapper, **_: object) -> None:
    """Cover a connection this thread opens while a handle watches every connection."""
    session = _local.session
    if session is None or not session.complete or connection in session.guards:
        return
    if all(held is not connection for held in connections.all(initialized_only=True)):
        return
    session.guard(connection)
    for watch in session.watches:
        if not watch.view:
            watch.cover(session, connection)


def awaits_write(connection: BaseDatabaseWrapper) -> bool:
    """Whether a view transaction on this thread waits on `connection` to open."""
    session = _local.session
    return session is not None and session.awaits(connection)


class LazySavepoint:
    """Protect one subtree, issuing no statement until the subtree runs one.

    The savepoint waits for the first statement, so a subtree that runs none costs none.
    """

    __slots__ = ("_aliases", "_closed", "_session", "_thread", "_watch")

    _session: _Session

    def __init__(
        self, aliases: Iterable[str] = (), *, atomic: AtomicMode = WRITES
    ) -> None:
        """Hold the aliases found inside an atomic block and the autocommit choice."""
        self._aliases = tuple(aliases)
        self._watch = self._watching(atomic)
        self._closed = False
        self._thread = 0

    def __enter__(self) -> Self:
        """Watch the given aliases, and every connection unless `atomic` is False."""
        self._thread = threading.get_ident()
        session = _local.session
        if session is None:
            session = _local.session = _Session()
        self._session = session
        session.watches.append(self._watch)
        try:
            self._cover(session)
        except BaseException:
            self._close(failed=True)
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Keep the subtree's work on a clean exit and undo it on an exception."""
        self._close(failed=exc_type is not None)

    def rollback(self) -> bool:
        """Undo what the subtree wrote, telling whether the outer transaction is usable.

        It is not when a statement failed before the subtree took its own savepoint.
        """
        self._close(failed=True)
        return not self._watch.broken

    def _watching(self, atomic: AtomicMode) -> _Watch:
        return _Watch(_opening_kind(atomic))

    def _cover(self, session: _Session) -> None:
        watch = self._watch
        for alias in self._aliases:
            watch.cover(session, connections[alias])
        if watch.opens != _NEVER:
            session.guard_every()
            for connection in tuple(session.guards):
                watch.cover(session, connection)

    def _close(self, *, failed: bool) -> None:
        if self._closed:
            return
        if threading.get_ident() != self._thread:
            msg = "A lazy savepoint must be closed on the thread that opened it."
            raise RuntimeError(msg)
        self._closed = True
        try:
            self._watch.settle(failed=failed)
        finally:
            self._session.leave(self._watch)


class LazyAtomic(LazySavepoint):
    """Run a view in a transaction on each alias, opened before its first write there.

    Reads before it run in autocommit, so a view that writes nothing opens nothing.
    """

    __slots__ = ()

    def __init__(self, aliases: Iterable[str]) -> None:
        """Hold the aliases, the first one settling first as nested blocks settle."""
        super().__init__(tuple(aliases)[::-1])

    @override
    def _watching(self, atomic: AtomicMode) -> _Watch:
        return _Watch(_opening_kind(atomic), view=True)

    @override
    def _cover(self, session: _Session) -> None:
        for alias in self._aliases:
            self._watch.cover(session, connections[alias])


__all__ = [
    "WRITES",
    "AtomicMode",
    "LazyAtomic",
    "LazySavepoint",
    "awaits_write",
    "cover_new_connection",
    "keep_savepoints",
]
