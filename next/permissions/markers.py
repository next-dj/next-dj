"""The predicates a permission is checked by and the access an endpoint declares."""

import inspect
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from django.db.models import Model


type Test = Callable[..., object]

type AccessKind = Literal["login", "permission"]

type AccessObject = str | type[Model]

_MAX_ARITY: Final = 2

_POSITIONAL: Final = frozenset(
    {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
)


@dataclass(frozen=True, slots=True)
class Predicate:
    """A rule about a user and an optional object, which gets the first `arity` of them.

    A rule over several conditions combines them with `and` and `or` in one function.
    """

    name: str
    test: Test
    arity: int = _MAX_ARITY

    def __call__(self, user: object, obj: object = None) -> bool:
        """Return whether `user` passes the rule on `obj`."""
        if self.arity == _MAX_ARITY:
            return bool(self.test(user, obj))
        if self.arity:
            return bool(self.test(user))
        return bool(self.test())


def signature_problem(test: Test) -> str | None:
    """Return why a predicate cannot call `test`, `None` when it takes up to two.

    A test takes `()`, `(user)` or `(user, obj)`, and `**kwargs` is harmless.
    """
    try:
        parameters = inspect.signature(test).parameters.values()
    except (TypeError, ValueError):
        return "has no signature Python can read"
    positional = 0
    for parameter in parameters:
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            return "takes *args"
        if (
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            and parameter.default is inspect.Parameter.empty
        ):
            return f"requires the keyword-only parameter {parameter.name!r}"
        positional += parameter.kind in _POSITIONAL
    if positional > _MAX_ARITY:
        return f"takes {positional} positional parameters"
    return None


def _arity(test: Test) -> int:
    """Return how many of `(user, obj)` a predicate passes `test`, two if unreadable."""
    try:
        parameters = inspect.signature(test).parameters.values()
    except (TypeError, ValueError):
        return _MAX_ARITY
    positional = sum(parameter.kind in _POSITIONAL for parameter in parameters)
    return min(positional, _MAX_ARITY)


def predicate(test: Test) -> Predicate:
    """Return a predicate over `test`, which takes `()`, `(user)` or `(user, obj)`.

    The arity is counted here once, and a signature outside the rule is `next.E159`.
    """
    if isinstance(test, Predicate):
        return test
    return Predicate(
        name=getattr(test, "__name__", repr(test)), test=test, arity=_arity(test)
    )


@dataclass(frozen=True, slots=True)
class Requirement:
    """One condition an endpoint declares, a login or permissions on an optional object.

    `obj` is a model whose row the URL names for a page, a context name for a component.
    """

    kind: AccessKind
    source: Path
    perms: tuple[str, ...] = ()
    obj: AccessObject | None = None

    def __post_init__(self) -> None:
        """Refuse a login carrying permissions or a permission carrying none."""
        if self.kind == "login" and (self.perms or self.obj is not None):
            msg = "A login requirement carries no permission and no object."
            raise ValueError(msg)
        if self.kind == "permission" and not self.perms:
            msg = "A permission requirement names at least one permission."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class AccessRule:
    """The requirements of one endpoint in the order its chain declared them.

    A public rule names the file that opened it in `sources`.
    """

    requirements: tuple[Requirement, ...] = ()
    public: bool = False
    sources: tuple[Path, ...] = ()

    @property
    def undeclared(self) -> bool:
        """Whether the rule asks nothing and opens nothing, so the default decides."""
        return not self.requirements and not self.public


@dataclass(frozen=True, slots=True)
class Denial:
    """Why a check refused, the requester logged out or short of a permission."""

    reason: AccessKind
    requirement: Requirement


OPEN: Final = AccessRule()


def joined(rules: Iterable[AccessRule], *, public: bool = False) -> AccessRule:
    """Return one rule holding the requirements of `rules` in order, each once.

    Rules joined side by side lift nothing of one another, unlike a chain.
    """
    requirements: dict[Requirement, None] = {}
    sources: dict[Path, None] = {}
    for rule in rules:
        requirements.update(dict.fromkeys(rule.requirements))
        sources.update(dict.fromkeys(rule.sources))
    if not requirements and not public:
        return OPEN
    return AccessRule(tuple(requirements), public, tuple(sources))


def chained(rules: Iterable[AccessRule]) -> AccessRule:
    """Return the rule a chain of files gives, root first, a public file lifting above.

    A file that pairs `public()` with its own requirements lifts nothing of the chain.
    """
    requirements: dict[Requirement, None] = {}
    opened: AccessRule | None = None
    for rule in rules:
        if rule.public and not rule.requirements and (requirements or opened is None):
            requirements.clear()
            opened = rule
        if rule.requirements:
            requirements.update(dict.fromkeys(rule.requirements))
    if requirements:
        sources = tuple(dict.fromkeys(item.source for item in requirements))
        return AccessRule(tuple(requirements), sources=sources)
    if opened is None:
        return OPEN
    return AccessRule(public=True, sources=opened.sources)


__all__ = [
    "OPEN",
    "AccessObject",
    "AccessRule",
    "Denial",
    "Requirement",
    "Test",
    "chained",
    "joined",
    "signature_problem",
]
