"""Transaction policy of next.dj views and lazy savepoints around rendered subtrees."""

import enum
import re
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
from types import TracebackType
from typing import Final, Literal, Self, TypeGuard, cast, override

from django.core.exceptions import ImproperlyConfigured
from django.db import Error, connections, transaction
from django.db.backends.base.base import BaseDatabaseWrapper
from django.http import HttpRequest
from django.utils.module_loading import import_string

from next.conf.defaults import DEFAULTS
from next.conf.scopes import scope_value
from next.conf.settings import next_framework_settings


SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
"""The methods that follow `TRANSACTIONS["SAFE"]`, every other one follows `UNSAFE`."""

ALL_ALIASES: Final = "all"
"""The `ALIASES` value that names every database alias Django holds a connection for."""

_SCOPE: Final = "TRANSACTIONS"
_RULE_SETTING: Final = f"NEXT_FRAMEWORK[{_SCOPE!r}]['RULE']"
_EXEMPT_ATTR: Final = "_non_atomic_requests"
_DUMMY: Final = "django.db.backends.dummy"


class ViewKind(enum.StrEnum):
    """The kind of next.dj view a request reached, as the transaction rule sees it."""

    PAGE = "page"
    ACTION = "action"
    ZONE = "zone"
    BOUNDARY = "boundary"
    NAVIGATION = "navigation"


type TransactionRule = Callable[[HttpRequest, ViewKind], Iterable[str]]
type AliasChoice = tuple[str, ...] | Literal["all"] | None


@dataclass(frozen=True, slots=True)
class AtomicOverride:
    """One view's own transaction choice, which wins over the method table and `RULE`.

    `aliases` of `None` takes `TRANSACTIONS["ALIASES"]`, and `"all"` every alias.
    """

    atomic: bool
    aliases: AliasChoice = None


@dataclass(frozen=True, slots=True)
class _Policy:
    """The `TRANSACTIONS` scope read once, aliases matched to the connections."""

    safe: tuple[str, ...]
    unsafe: tuple[str, ...]
    aliases: tuple[str, ...]
    every: tuple[str, ...]
    rule: TransactionRule | None


def flag_usable(value: object) -> bool:
    """Whether `value` is a usable `SAFE` or `UNSAFE` value."""
    return isinstance(value, bool)


def alias_list(value: object) -> TypeGuard[list[str] | tuple[str, ...]]:
    """Whether `value` is a list or a tuple of alias names."""
    return isinstance(value, list | tuple) and all(isinstance(n, str) for n in value)


def aliases_usable(value: object) -> bool:
    """Whether `value` is a usable `ALIASES` value."""
    return value == ALL_ALIASES or alias_list(value)


def override_problem(atomic: object, aliases: object) -> str | None:
    """Return why `atomic` and `aliases` make no override, `None` when they make one."""
    if not isinstance(atomic, bool):
        return f"atomic must be True or False, got {type(atomic).__name__!r}"
    if aliases is not None and not aliases_usable(aliases):
        return (
            "aliases must be None, 'all' or a list or tuple of database aliases, "
            f"got {aliases!r}"
        )
    return None


def _choice(aliases: object) -> AliasChoice:
    """Return a checked `aliases` value in the shape `AtomicOverride` holds."""
    if alias_list(aliases):
        return tuple(aliases)
    return ALL_ALIASES if aliases == ALL_ALIASES else None


def override_of(atomic: object, aliases: object) -> AtomicOverride | None:
    """Return the override `atomic` and `aliases` make, `None` for an unusable pair."""
    if override_problem(atomic, aliases) is not None:
        return None
    return AtomicOverride(atomic=bool(atomic), aliases=_choice(aliases))


def rule_usable(value: object) -> bool:
    """Whether `value` is a usable `RULE` value, before its path is imported."""
    return value is None or isinstance(value, str)


def import_rule(dotted: str) -> TransactionRule:
    """Import the `RULE` callable, raising `ImproperlyConfigured` for a broken path."""
    try:
        target = import_string(dotted)
    except ImportError as exc:
        msg = f"{_RULE_SETTING} {dotted!r} could not be imported: {exc}."
        raise ImproperlyConfigured(msg) from exc
    if not callable(target):
        msg = f"{_RULE_SETTING} {dotted!r} names no callable."
        raise ImproperlyConfigured(msg)
    return cast("TransactionRule", target)


def _flag(key: str) -> bool:
    """Return one bool key of the scope, its default for a value of another type."""
    value = scope_value(_SCOPE, key)
    return value if isinstance(value, bool) else bool(DEFAULTS[_SCOPE][key])


def _present(names: Iterable[str], every: tuple[str, ...]) -> tuple[str, ...]:
    """Return the names Django holds a connection for, in order and without repeats."""
    return tuple(dict.fromkeys(name for name in names if name in every))


def _aliases(value: object, every: tuple[str, ...]) -> tuple[str, ...]:
    """Return the aliases an `ALIASES` value names, the default for an unusable one."""
    if value == ALL_ALIASES:
        return every
    if alias_list(value):
        return _present(value, every)
    return _present(DEFAULTS[_SCOPE]["ALIASES"], every)


def _read_policy(every: tuple[str, ...]) -> _Policy:
    aliases = _aliases(scope_value(_SCOPE, "ALIASES"), every)
    rule = scope_value(_SCOPE, "RULE")
    return _Policy(
        safe=aliases if _flag("SAFE") else (),
        unsafe=aliases if _flag("UNSAFE") else (),
        aliases=aliases,
        every=every,
        rule=import_rule(rule) if isinstance(rule, str) else None,
    )


class _PolicyMemo:
    """Hold the policy while the merged scope and the connection settings stay the same.

    A settings reload or new connection settings replace either object, so no receiver.
    """

    __slots__ = ("held",)

    def __init__(self) -> None:
        """Start empty, the first read builds the policy."""
        self.held: tuple[object, object, _Policy] | None = None

    def get(self) -> _Policy:
        """Return the policy of the current settings, building it on a miss."""
        scope = next_framework_settings.TRANSACTIONS
        databases = connections.settings
        held = self.held
        if held is not None and held[0] is scope and held[1] is databases:
            return held[2]
        policy = _read_policy(_backed(databases))
        self.held = (scope, databases, policy)
        return policy


def _backed(databases: Mapping[str, Mapping[str, object]]) -> tuple[str, ...]:
    """Return the aliases with a real backend, not the dummy of an empty DATABASES."""
    return tuple(name for name, db in databases.items() if db["ENGINE"] != _DUMMY)


_policy = _PolicyMemo()


def _override_aliases(override: AtomicOverride, policy: _Policy) -> tuple[str, ...]:
    if not override.atomic:
        return ()
    if override.aliases is None:
        return policy.aliases
    if isinstance(override.aliases, str):
        return policy.every
    return _present(override.aliases, policy.every)


def _rule_aliases(answer: Iterable[str], every: tuple[str, ...]) -> tuple[str, ...]:
    """Return what `RULE` answered as aliases, one name read as a one-item tuple."""
    return _present((answer,) if isinstance(answer, str) else answer, every)


def atomic_aliases(
    request: HttpRequest, kind: ViewKind, override: AtomicOverride | None = None
) -> tuple[str, ...]:
    """Return the database aliases whose transaction wraps this request, in order.

    An alias Django holds no connection for is skipped, in what `RULE` answers too.
    """
    policy = _policy.get()
    if override is not None:
        return _override_aliases(override, policy)
    if policy.rule is not None:
        return _rule_aliases(policy.rule(request, kind), policy.every)
    return policy.safe if request.method in SAFE_METHODS else policy.unsafe


def safe_aliases(override: AtomicOverride | None = None) -> tuple[str, ...] | None:
    """Return the aliases every safe request of one view opens, decided ahead of it.

    `None` stands for a `RULE`, which answers per request and so cannot be held.
    """
    policy = _policy.get()
    if override is not None:
        return _override_aliases(override, policy)
    return None if policy.rule is not None else policy.safe


@contextmanager
def _atomic_chain(aliases: tuple[str, ...]) -> Iterator[None]:
    with ExitStack() as stack:
        for alias in reversed(aliases):
            stack.enter_context(transaction.atomic(using=alias))
        yield


_NO_ATOMIC: Final[AbstractContextManager[None]] = nullcontext()


def request_atomic(aliases: tuple[str, ...]) -> AbstractContextManager[None]:
    """Open `transaction.atomic` on each alias, the last one outermost as Django nests.

    The blocks open on entry, so an `on_commit` hook set before any query awaits commit.
    """
    if not aliases:
        return _NO_ATOMIC
    return _atomic_chain(aliases)


class _EveryAlias(set[str]):
    """An exemption that holds every alias, those Django connects to later included.

    It stays a set, so `transaction.non_atomic_requests` applied afterwards still adds.
    """

    __slots__ = ()

    @override
    def __contains__(self, alias: object) -> bool:
        return True


def skip_atomic_requests[V: Callable[..., object]](view: V) -> V:
    """Mark `view` so Django's `ATOMIC_REQUESTS` wrapping skips it on every alias.

    It marks the callable passed in, so apply it to the final view after any wrapping.
    """
    vars(view)[_EXEMPT_ATTR] = _EveryAlias()
    return view


def probe_atomic_aliases() -> tuple[str, ...]:
    """Return the aliases whose connection on this thread is inside an atomic block.

    Only the connections this thread already holds are read, so the probe opens none.
    """
    return tuple(
        connection.alias
        for connection in connections.all(initialized_only=True)
        if connection.in_atomic_block
    )


class StatementKind(enum.IntEnum):
    """What one SQL statement does, as the lazy savepoint decides on it."""

    CONTROL = 0
    STANDALONE = 1
    SESSION = 2
    READ = 3
    WRITE = 4


WRITES: Final = "writes"
"""The `atomic` value that opens a transaction before the first write in autocommit."""

type AtomicMode = bool | Literal["writes"]

_NEVER: Final = StatementKind.WRITE + 1
_LEAD: Final = re.compile(r"(?:\s|\(|/\*.*?\*/|--[^\n]*+)*+([A-Za-z]+)", re.DOTALL)
_TOKEN: Final = re.compile(
    r"\s++|/\*.*?\*/|--[^\n]*+|'(?:[^']|'')*+'|\"(?:[^\"]|\"\")*+\"|`(?:[^`]|``)*+`"
    r"|\$(?P<tag>\w*)\$.*?\$(?P=tag)\$|(?P<word>[A-Za-z_][\w$]*+)|(?P<bad>['\"`]|/\*)|.",
    re.DOTALL,
)
_HINT: Final = re.compile(
    r"\b(?:INTO|UPDATE|SHARE|INSERT|DELETE|MERGE)\b", re.IGNORECASE
)
_LEADS: Final = {
    "START": StatementKind.CONTROL,
    "COMMIT": StatementKind.CONTROL,
    "END": StatementKind.CONTROL,
    "ROLLBACK": StatementKind.CONTROL,
    "RELEASE": StatementKind.CONTROL,
    "ABORT": StatementKind.CONTROL,
    "VACUUM": StatementKind.STANDALONE,
    "ATTACH": StatementKind.STANDALONE,
    "DETACH": StatementKind.STANDALONE,
    "SAVEPOINT": StatementKind.SESSION,
    "SET": StatementKind.SESSION,
    "RESET": StatementKind.SESSION,
    "PRAGMA": StatementKind.SESSION,
    "SELECT": StatementKind.READ,
    "SHOW": StatementKind.READ,
    "EXPLAIN": StatementKind.READ,
    "WITH": StatementKind.READ,
    "VALUES": StatementKind.READ,
    "TABLE": StatementKind.READ,
    "DESCRIBE": StatementKind.READ,
    "DESC": StatementKind.READ,
}
_FOLLOWED: Final = frozenset(
    {"ALTER", "BEGIN", "CLUSTER", "CREATE", "DISCARD", "DROP", "REINDEX"}
)
_BEGIN_MODES: Final = frozenset(
    {
        "DEFERRABLE",
        "DEFERRED",
        "EXCLUSIVE",
        "IMMEDIATE",
        "ISOLATION",
        "NOT",
        "READ",
        "TRAN",
        "TRANSACTION",
        "WORK",
    }
)
_ALONE: Final = frozenset({"DATABASE", "SUBSCRIPTION", "SYSTEM", "TABLESPACE"})
_CHANGES: Final = frozenset({"DELETE", "INSERT", "INTO", "MERGE"})
_LOCKED: Final = frozenset({"SHARE", "UPDATE"})
_LOCKING: Final = frozenset({"FOR", "IN", "KEY"})
_LOOSE: Final = frozenset({"EXPLAIN", "WITH"})


def _text(sql: object) -> str | None:
    """Return the statement as text, `None` for one that is neither text nor UTF-8."""
    if isinstance(sql, str):
        return sql
    if isinstance(sql, bytes):
        try:
            return sql.decode()
        except UnicodeDecodeError:
            return None
    return None


def _words(text: str, start: int) -> tuple[str, ...] | None:
    """Return the bare words after `start`, `None` if a quote or comment never ends."""
    words: list[str] = []
    for token in _TOKEN.finditer(text, start):
        group = token.lastgroup
        if group == "bad":
            return None
        if group == "word":
            words.append(token.group("word").upper())
    return tuple(words)


def _changes(lead: str, words: tuple[str, ...]) -> bool:
    """Whether a read changes data or locks rows, judged by its bare words."""
    loose = lead in _LOOSE
    before = ""
    for word in words:
        if word in _CHANGES or (word in _LOCKED and (loose or before in _LOCKING)):
            return True
        before = word
    return False


def _read(text: str, start: int, lead: str) -> StatementKind:
    if _HINT.search(text, start) is None:
        return StatementKind.READ
    words = _words(text, start)
    if words is None or _changes(lead, words):
        return StatementKind.WRITE
    return StatementKind.READ


def _followed(lead: str, words: tuple[str, ...]) -> StatementKind:
    """Classify a statement whose leading keyword alone does not decide."""
    if lead == "BEGIN":
        control = not words or words[0] in _BEGIN_MODES
        return StatementKind.CONTROL if control else StatementKind.WRITE
    if lead == "DISCARD":
        return StatementKind.STANDALONE if "ALL" in words else StatementKind.SESSION
    if lead == "CLUSTER":
        alone = set(words) <= {"VERBOSE"}
    else:
        alone = "CONCURRENTLY" in words or not _ALONE.isdisjoint(words[:2])
    return StatementKind.STANDALONE if alone else StatementKind.WRITE


def statement_kind(sql: object) -> StatementKind:
    """Classify a statement by its leading keyword, an unknown one counting as a write.

    Comments, quoted text and quoted names never decide, so only real keywords count.
    """
    text = _text(sql)
    if text is None:
        return StatementKind.WRITE
    lead = _LEAD.match(text)
    if lead is None:
        return StatementKind.WRITE
    word = lead.group(1).upper()
    kind = _LEADS.get(word)
    if kind is StatementKind.READ:
        return _read(text, lead.end(), word)
    if kind is not None:
        return kind
    if word not in _FOLLOWED:
        return StatementKind.WRITE
    words = _words(text, lead.end())
    return StatementKind.WRITE if words is None else _followed(word, words)


def _nests(kind: StatementKind, sql: object) -> bool:
    """Whether the statement opens a savepoint, which every later one nests inside."""
    if kind is not StatementKind.SESSION:
        return False
    lead = _LEAD.match(_text(sql) or "")
    return lead is not None and lead.group(1).upper() == "SAVEPOINT"


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

    __slots__ = ("acted", "block", "broken", "inside", "opens", "sid")

    def __init__(self, opens: int, *, inside: bool) -> None:
        """Record the autocommit choice and whether a transaction is already open."""
        self.opens = opens
        self.inside = inside
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

    def push(self, frame: _Frame) -> None:
        """Start watching one more subtree, the innermost so far."""
        self.frames.append(frame)
        self.pending += 1

    def _act(self, kind: StatementKind, sql: object) -> _Opening | None:
        """Take what the waiting subtrees need, returning the transaction opened now.

        A read needs one savepoint, a write or a foreign savepoint one per subtree.
        """
        connection = self.connection
        waiting = [frame for frame in self.frames if not frame.acted]
        if connection.in_atomic_block:
            if not transaction.get_rollback(using=connection.alias):
                each = kind is StatementKind.WRITE or _nests(kind, sql)
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
    """The frames one handle pushed, one per connection it covers."""

    __slots__ = ("entries", "opens")

    def __init__(self, opens: int) -> None:
        """Start covering no connection."""
        self.opens = opens
        self.entries: dict[BaseDatabaseWrapper, tuple[_Guard, _Frame]] = {}

    def cover(self, session: "_Session", connection: BaseDatabaseWrapper) -> None:
        """Push a frame for `connection` unless it has one or the subtree needs none."""
        inside = connection.in_atomic_block
        if (not inside and self.opens == _NEVER) or connection in self.entries:
            return
        guard = session.guard(connection)
        frame = _Frame(self.opens, inside=inside)
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
        return guard

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
        watch.cover(session, connection)


class LazySavepoint:
    """Protect one subtree, issuing no statement until the subtree runs one.

    In autocommit, a write in an atomic block opened before the handle's transaction
    commits with that block, not with the subtree. A durable block after the subtree's
    first write raises Django's RuntimeError, except under a TestCase. A savepoint
    rollback keeps the on_commit hooks the subtree added, and once a write lands in the
    handle's own transaction, a caught database error dooms it.
    """

    __slots__ = ("_aliases", "_closed", "_session", "_thread", "_watch")

    _session: _Session

    def __init__(
        self, aliases: Iterable[str] = (), *, atomic: AtomicMode = WRITES
    ) -> None:
        """Hold the aliases found inside an atomic block and the autocommit choice."""
        self._aliases = tuple(aliases)
        self._watch = _Watch(_opening_kind(atomic))
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


def lazy_savepoint(
    aliases: Iterable[str] = (), *, atomic: AtomicMode = WRITES
) -> LazySavepoint:
    """Return the handle that protects one subtree, see `LazySavepoint`."""
    return LazySavepoint(aliases, atomic=atomic)


__all__ = [
    "ALL_ALIASES",
    "SAFE_METHODS",
    "WRITES",
    "AliasChoice",
    "AtomicMode",
    "AtomicOverride",
    "LazySavepoint",
    "StatementKind",
    "TransactionRule",
    "ViewKind",
    "atomic_aliases",
    "cover_new_connection",
    "keep_savepoints",
    "lazy_savepoint",
    "probe_atomic_aliases",
    "request_atomic",
    "skip_atomic_requests",
    "statement_kind",
]
