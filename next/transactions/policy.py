"""The `TRANSACTIONS` rule that names the database aliases a next.dj view wraps."""

import enum
from collections.abc import Callable, Generator, Iterable, Mapping
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
from typing import Final, Literal, TypeGuard, cast, override

from django.core.exceptions import ImproperlyConfigured
from django.db import connections, transaction
from django.http import HttpRequest
from django.utils.module_loading import import_string

from next.conf.defaults import DEFAULTS
from next.conf.defer import atomic_choice
from next.conf.settings import next_framework_settings
from next.conf.shapes import EMPTY, Section, Shape, alternatives, pick, shown

from .savepoints import WRITES, AtomicMode, LazyAtomic, awaits_write


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


type AliasChoice = tuple[str, ...] | Literal["all"] | None


@dataclass(frozen=True, slots=True)
class AtomicOverride:
    """One view's own transaction choice, which wins over the method table and `RULE`.

    `aliases` of `None` takes `TRANSACTIONS["ALIASES"]`, and `"all"` every alias.
    """

    atomic: AtomicMode
    aliases: AliasChoice = None


type TransactionRule = Callable[[HttpRequest, ViewKind], Iterable[str] | AtomicOverride]


@dataclass(frozen=True, slots=True)
class AtomicScope:
    """The transaction one request runs in, the aliases it wraps and when it opens.

    No alias means no transaction, whatever `atomic` holds.
    """

    aliases: tuple[str, ...]
    atomic: AtomicMode


_NO_SCOPE: Final = AtomicScope((), atomic=False)


def _scope(aliases: tuple[str, ...], atomic: AtomicMode) -> AtomicScope:
    """Return the scope of `aliases` opened as `atomic` says, no aliases for none."""
    if atomic is False or not aliases:
        return _NO_SCOPE
    return AtomicScope(aliases, atomic)


@dataclass(frozen=True, slots=True)
class _Policy:
    """The `TRANSACTIONS` scope read once, aliases matched to the connections."""

    safe: AtomicScope
    unsafe: AtomicScope
    aliases: tuple[str, ...]
    every: tuple[str, ...]
    rule: TransactionRule | None


def alias_list(value: object) -> TypeGuard[list[str] | tuple[str, ...]]:
    """Whether `value` is a list or a tuple of alias names."""
    return isinstance(value, list | tuple) and all(isinstance(n, str) for n in value)


def aliases_usable(
    value: object,
) -> TypeGuard[Literal["all"] | list[str] | tuple[str, ...]]:
    """Whether `value` is a usable `ALIASES` value."""
    return value == ALL_ALIASES or alias_list(value)


_MODES: Final = alternatives((True, False, WRITES))


def override_problem(atomic: object, aliases: object) -> str | None:
    """Return why `atomic` and `aliases` make no override, `None` when they make one."""
    if not atomic_choice(atomic):
        return f"atomic must be {_MODES}, got {shown(atomic)}"
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
    if not atomic_choice(atomic) or override_problem(atomic, aliases) is not None:
        return None
    return AtomicOverride(atomic=atomic, aliases=_choice(aliases))


def rule_usable(value: object) -> TypeGuard[str | None]:
    """Whether `value` is a usable `RULE` value, before its path is imported."""
    return value is None or isinstance(value, str)


SHAPE: Final = Section(
    {
        "SAFE": Shape(atomic_choice, _MODES),
        "UNSAFE": Shape(atomic_choice, _MODES),
        "ALIASES": Shape(aliases_usable, "'all' or a list of database aliases"),
        "RULE": Shape(rule_usable, "the dotted path of a callable or None"),
    }
)
"""The shape of the `TRANSACTIONS` scope, which the checks and the policy read."""


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


def _present(names: Iterable[str], every: tuple[str, ...]) -> tuple[str, ...]:
    """Return the names Django holds a connection for, in order and without repeats."""
    return tuple(dict.fromkeys(name for name in names if name in every))


def _read_policy(scope: object, every: tuple[str, ...]) -> _Policy:
    """Return the policy `scope` names, each unusable value read as its default."""
    held = scope if isinstance(scope, Mapping) else EMPTY
    defaults = DEFAULTS[_SCOPE]
    named = pick(held, "ALIASES", aliases_usable, defaults)
    aliases = every if named == ALL_ALIASES else _present(named, every)
    rule = pick(held, "RULE", rule_usable, defaults)
    return _Policy(
        safe=_scope(aliases, pick(held, "SAFE", atomic_choice, defaults)),
        unsafe=_scope(aliases, pick(held, "UNSAFE", atomic_choice, defaults)),
        aliases=aliases,
        every=every,
        rule=None if rule is None else import_rule(rule),
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
        policy = _read_policy(scope, _backed(databases))
        self.held = (scope, databases, policy)
        return policy


def _backed(databases: Mapping[str, Mapping[str, object]]) -> tuple[str, ...]:
    """Return the aliases with a real backend, not the dummy of an empty DATABASES."""
    return tuple(name for name, db in databases.items() if db["ENGINE"] != _DUMMY)


_policy = _PolicyMemo()


def _override_scope(override: AtomicOverride, policy: _Policy) -> AtomicScope:
    if override.aliases is None:
        aliases = policy.aliases
    elif isinstance(override.aliases, str):
        aliases = policy.every
    else:
        aliases = _present(override.aliases, policy.every)
    return _scope(aliases, override.atomic)


def _rule_scope(answer: Iterable[str] | AtomicOverride, policy: _Policy) -> AtomicScope:
    """Return the scope `RULE` answered, aliases alone opening on entry."""
    if not isinstance(answer, AtomicOverride):
        names = (answer,) if isinstance(answer, str) else answer
        return _scope(_present(names, policy.every), atomic=True)
    problem = override_problem(answer.atomic, answer.aliases)
    if problem is not None:
        msg = f"{_RULE_SETTING} answered an override that applies nothing, {problem}."
        raise ImproperlyConfigured(msg)
    return _override_scope(answer, policy)


def atomic_scope(
    request: HttpRequest, kind: ViewKind, override: AtomicOverride | None = None
) -> AtomicScope:
    """Return the transaction this request runs in, its aliases in order.

    An alias Django holds no connection for is skipped, in what `RULE` answers too.
    """
    policy = _policy.get()
    if override is not None:
        return _override_scope(override, policy)
    if policy.rule is not None:
        return _rule_scope(policy.rule(request, kind), policy)
    return policy.safe if request.method in SAFE_METHODS else policy.unsafe


def safe_aliases(override: AtomicOverride | None = None) -> tuple[str, ...] | None:
    """Return the aliases every safe request of one view opens, decided ahead of it.

    `None` stands for a `RULE`, which answers per request and so cannot be held.
    """
    policy = _policy.get()
    if override is not None:
        return _override_scope(override, policy).aliases
    return None if policy.rule is not None else policy.safe.aliases


@contextmanager
def _atomic_chain(aliases: tuple[str, ...]) -> Generator[None]:
    with ExitStack() as stack:
        for alias in reversed(aliases):
            stack.enter_context(transaction.atomic(using=alias))
        yield


_NO_ATOMIC: Final[AbstractContextManager[object]] = nullcontext()


def request_atomic(scope: AtomicScope) -> AbstractContextManager[object]:
    """Open the transaction of `scope`, the last alias outermost as Django nests them.

    `True` opens on entry, so an `on_commit` hook set before any query awaits commit.
    """
    if not scope.aliases or scope.atomic is False:
        return _NO_ATOMIC
    if scope.atomic is True:
        aliases = scope.aliases
        if len(aliases) == 1:
            return transaction.atomic(using=aliases[0])
        return _atomic_chain(aliases)
    return LazyAtomic(scope.aliases)


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

    A view transaction still waiting for its first write counts, as it opens one later.
    """
    return tuple(
        connection.alias
        for connection in connections.all(initialized_only=True)
        if connection.in_atomic_block or awaits_write(connection)
    )


__all__ = [
    "ALL_ALIASES",
    "SAFE_METHODS",
    "SHAPE",
    "AliasChoice",
    "AtomicOverride",
    "AtomicScope",
    "TransactionRule",
    "ViewKind",
    "alias_list",
    "aliases_usable",
    "atomic_scope",
    "import_rule",
    "override_of",
    "override_problem",
    "probe_atomic_aliases",
    "request_atomic",
    "rule_usable",
    "safe_aliases",
    "skip_atomic_requests",
]
