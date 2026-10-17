"""The predicates a permission is checked by and the access an endpoint declares."""

import inspect
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from django.db.models import Model


type Operator = Literal["&", "|", "~"]

type Test = Callable[..., object]

type AccessKind = Literal["login", "permission"]

type AccessObject = str | type[Model]

_MAX_ARITY: Final = 2

_POSITIONAL: Final = frozenset(
    {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
)

_MIN_JOINED: Final = 2


@dataclass(frozen=True, slots=True)
class Predicate:
    """A rule about a user and an optional object, a leaf callable or a composite.

    A leaf gets the first `arity` of `(user, obj)`, and a composite short-circuits.
    """

    name: str
    test: Test | None = None
    arity: int = _MAX_ARITY
    operator: Operator | None = None
    operands: tuple["Predicate", ...] = ()

    def __post_init__(self) -> None:
        """Refuse a shape that is neither a leaf nor a composite of its operator."""
        problem = _shape_problem(self)
        if problem is not None:
            msg = f"The predicate {self.name!r} {problem}."
            raise ValueError(msg)

    def __call__(self, user: object, obj: object = None) -> bool:
        """Return whether `user` passes the rule on `obj`."""
        if self.test is not None:
            if self.arity == _MAX_ARITY:
                return bool(self.test(user, obj))
            if self.arity:
                return bool(self.test(user))
            return bool(self.test())
        if self.operator == "&":
            return all(operand(user, obj) for operand in self.operands)
        if self.operator == "|":
            return any(operand(user, obj) for operand in self.operands)
        return not self.operands[0](user, obj)

    def __and__(self, other: object) -> "Predicate":
        """Return the rule both predicates must pass."""
        if not isinstance(other, Predicate):
            return NotImplemented
        return _joined("&", self, other)

    def __or__(self, other: object) -> "Predicate":
        """Return the rule either predicate may pass."""
        if not isinstance(other, Predicate):
            return NotImplemented
        return _joined("|", self, other)

    def __invert__(self) -> "Predicate":
        """Return the rule this predicate must fail."""
        return Predicate(name=f"~{self.name}", operator="~", operands=(self,))

    def tests(self) -> Iterator[tuple[str, Test]]:
        """Yield the name and the callable of every leaf, left to right."""
        if self.test is not None:
            yield self.name, self.test
            return
        for operand in self.operands:
            yield from operand.tests()


def _shape_problem(rule: Predicate) -> str | None:
    """Return why `rule` is neither a leaf nor a composite, `None` when it is one."""
    if rule.test is not None:
        return _leaf_problem(rule)
    if rule.operator is None:
        return "holds neither a test nor an operator"
    return _composite_problem(rule)


def _leaf_problem(rule: Predicate) -> str | None:
    if rule.operator is not None or rule.operands:
        return "holds a test and an operator or operands at once"
    if not 0 <= rule.arity <= _MAX_ARITY:
        return f"passes {rule.arity} arguments to its test, not 0 to {_MAX_ARITY}"
    return None


def _composite_problem(rule: Predicate) -> str | None:
    if rule.operator == "~":
        return None if len(rule.operands) == 1 else "inverts other than one operand"
    if len(rule.operands) < _MIN_JOINED:
        return f"joins fewer than two operands with {rule.operator!r}"
    return None


def signature_problem(test: Test) -> str | None:
    """Return why `test` cannot be a leaf, `None` when it takes zero to two arguments.

    A leaf takes `()`, `(user)` or `(user, obj)`, and `**kwargs` is harmless.
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
    """Return how many of `(user, obj)` a leaf passes `test`, two when unreadable."""
    try:
        parameters = inspect.signature(test).parameters.values()
    except (TypeError, ValueError):
        return _MAX_ARITY
    positional = sum(parameter.kind in _POSITIONAL for parameter in parameters)
    return min(positional, _MAX_ARITY)


def _joined(operator: Operator, left: Predicate, right: Predicate) -> Predicate:
    return Predicate(
        name=f"({left.name} {operator} {right.name})",
        operator=operator,
        operands=(left, right),
    )


def predicate(test: Test) -> Predicate:
    """Return a leaf predicate over `test`, which takes `()`, `(user)` or `(user, obj)`.

    The arity is counted here once, and a signature outside the rule is `next.E161`.
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

    A public rule names the file that opened it in `sources` and those it lifted.
    """

    requirements: tuple[Requirement, ...] = ()
    public: bool = False
    sources: tuple[Path, ...] = ()
    lifted: tuple[Path, ...] = ()


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
    lifted: dict[Path, None] = {}
    for rule in rules:
        requirements.update(dict.fromkeys(rule.requirements))
        sources.update(dict.fromkeys(rule.sources))
        lifted.update(dict.fromkeys(rule.lifted))
    if not requirements and not public:
        return OPEN
    kept = tuple(lifted) if public else ()
    return AccessRule(tuple(requirements), public, tuple(sources), kept)


def chained(rules: Iterable[AccessRule]) -> AccessRule:
    """Return the rule a chain of files gives, root first, a public file lifting above.

    A file that pairs `public()` with its own requirements lifts nothing of the chain.
    """
    requirements: dict[Requirement, None] = {}
    opened: AccessRule | None = None
    lifted: tuple[Path, ...] = ()
    for rule in rules:
        if rule.public and not rule.requirements and (requirements or opened is None):
            lifted = tuple(dict.fromkeys(item.source for item in requirements))
            requirements.clear()
            opened = rule
        if rule.requirements:
            requirements.update(dict.fromkeys(rule.requirements))
            opened = None
    if requirements:
        sources = tuple(dict.fromkeys(item.source for item in requirements))
        return AccessRule(tuple(requirements), sources=sources)
    if opened is None:
        return OPEN
    return AccessRule(public=True, sources=opened.sources, lifted=lifted)


__all__ = [
    "OPEN",
    "AccessObject",
    "AccessRule",
    "Denial",
    "Operator",
    "Predicate",
    "Requirement",
    "Test",
    "chained",
    "joined",
    "predicate",
    "signature_problem",
]
