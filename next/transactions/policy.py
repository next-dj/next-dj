"""The `TRANSACTIONS` rule that names the database aliases a next.dj view wraps."""

from collections.abc import Callable, Generator, Iterable, Mapping
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
from typing import Final, Literal, cast

from django.core.exceptions import ImproperlyConfigured
from django.db import connections, transaction
from django.http import HttpRequest
from django.utils.module_loading import import_string

from next.caches import SETTINGS, Memo
from next.conf.defaults import AUTO, DEFAULTS
from next.conf.settings import next_framework_settings
from next.conf.shapes import EMPTY, alternatives, enforce, pick, shown
from next.conf.transactions import (
    ALL_ALIASES,
    SCOPE,
    SHAPE,
    alias_list,
    aliases_usable,
    atomic_choice,
    method_choice,
    rule_usable,
)
from next.requests import RequestKind

from .savepoints import WRITES, AtomicMode, LazyAtomic, awaits_write


SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
"""The methods that follow `TRANSACTIONS["SAFE"]`, every other one follows `UNSAFE`."""

_PREFIX: Final = f"NEXT_FRAMEWORK[{SCOPE!r}]"
_RULE_SETTING: Final = f"{_PREFIX}['RULE']"
_DUMMY: Final = "django.db.backends.dummy"


type AliasChoice = tuple[str, ...] | Literal["all"] | None

type MethodChoice = AtomicMode | Literal["auto"]


@dataclass(frozen=True, slots=True)
class AtomicOverride:
    """One view's own transaction choice, which wins over the method table and `RULE`.

    `aliases=None` reads `TRANSACTIONS["ALIASES"]`, and `"all"` every alias.
    """

    atomic: AtomicMode
    aliases: AliasChoice = None


type TransactionRule = Callable[
    [HttpRequest, RequestKind], Iterable[str] | AtomicOverride
]


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
    requested: tuple[str, ...]
    rule: TransactionRule | None


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


def _method_scope(
    mode: MethodChoice,
    aliases: tuple[str, ...],
    requested: tuple[str, ...],
    *,
    unsafe: bool,
) -> AtomicScope:
    """Return the scope one side of the method table opens.

    `"auto"` wraps the `ATOMIC_REQUESTS` aliases, and on an unsafe method `ALIASES`.
    """
    if mode == AUTO:
        names = (*aliases, *requested) if unsafe else requested
        return _scope(tuple(dict.fromkeys(names)), atomic=True)
    return _scope(aliases, mode)


def _read_policy(
    scope: object, every: tuple[str, ...], requested: tuple[str, ...]
) -> _Policy:
    """Return the policy `scope` names, refusing a value outside its shape."""
    held = scope if isinstance(scope, Mapping) else EMPTY
    enforce(_PREFIX, held, SHAPE)
    defaults = DEFAULTS[SCOPE]
    named = pick(held, "ALIASES", aliases_usable, defaults)
    aliases = every if named == ALL_ALIASES else _present(named, every)
    rule = pick(held, "RULE", rule_usable, defaults)
    safe = pick(held, "SAFE", method_choice, defaults)
    unsafe = pick(held, "UNSAFE", method_choice, defaults)
    return _Policy(
        safe=_method_scope(safe, aliases, requested, unsafe=False),
        unsafe=_method_scope(unsafe, aliases, requested, unsafe=True),
        aliases=aliases,
        every=every,
        requested=requested,
        rule=None if rule is None else import_rule(rule),
    )


_HELD: Final[Memo[_Policy]] = Memo(depends=(SETTINGS,))


def _current() -> _Policy:
    """Return the policy of the current settings, building it on a miss.

    The `ATOMIC_REQUESTS` flags are read on every call, as Django reads them per view.
    """
    databases = connections.settings
    requested = atomic_requests(databases)

    def build() -> _Policy:
        return _read_policy(
            next_framework_settings.TRANSACTIONS, _backed(databases), requested
        )

    policy = _HELD.get(build)
    if policy.requested != requested:
        _HELD.forget()
        policy = _HELD.get(build)
    return policy


def atomic_requests(databases: Mapping[str, Mapping[str, object]]) -> tuple[str, ...]:
    """Return the aliases `ATOMIC_REQUESTS` wraps, in the order Django nests them."""
    return tuple(
        name
        for name, db in databases.items()
        if db.get("ATOMIC_REQUESTS") and db.get("ENGINE") != _DUMMY
    )


def _backed(databases: Mapping[str, Mapping[str, object]]) -> tuple[str, ...]:
    """Return the aliases with a real backend, not the dummy of an empty DATABASES."""
    return tuple(name for name, db in databases.items() if db["ENGINE"] != _DUMMY)


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
    request: HttpRequest, kind: RequestKind, override: AtomicOverride | None = None
) -> AtomicScope:
    """Return the transaction this request runs in, its aliases in order.

    An alias Django holds no connection for is skipped, in what `RULE` answers too.
    """
    policy = _current()
    if override is not None:
        return _override_scope(override, policy)
    if policy.rule is not None:
        return _rule_scope(policy.rule(request, kind), policy)
    return policy.safe if request.method in SAFE_METHODS else policy.unsafe


def safe_aliases(override: AtomicOverride | None = None) -> tuple[str, ...] | None:
    """Return the aliases every safe request of one view opens, decided ahead of it.

    `None` stands for a `RULE`, which answers per request and so cannot be held.
    """
    policy = _current()
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

    `True` opens on entry, `"writes"` before the first write or `on_commit` hook.
    """
    if not scope.aliases or scope.atomic is False:
        return _NO_ATOMIC
    if scope.atomic is True:
        aliases = scope.aliases
        if len(aliases) == 1:
            return transaction.atomic(using=aliases[0])
        return _atomic_chain(aliases)
    return LazyAtomic(scope.aliases)


def skip_atomic_requests[V: Callable[..., object]](view: V) -> V:
    """Mark `view` so Django's `ATOMIC_REQUESTS` wrapping skips it on every alias.

    The next.dj policy owns each alias, so `non_atomic_requests` marks every one.
    """
    for alias in connections:
        view = transaction.non_atomic_requests(using=alias)(view)
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
    "SAFE_METHODS",
    "AliasChoice",
    "AtomicOverride",
    "AtomicScope",
    "MethodChoice",
    "TransactionRule",
    "atomic_requests",
    "atomic_scope",
    "import_rule",
    "override_of",
    "override_problem",
    "probe_atomic_aliases",
    "request_atomic",
    "safe_aliases",
    "skip_atomic_requests",
]
