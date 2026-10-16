"""The predicates a permission is checked by, composable with `&`, `|` and `~`."""

import inspect
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Final, Literal


type Operator = Literal["&", "|", "~"]

type Test = Callable[..., object]

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


__all__ = ["Operator", "Predicate", "Test", "predicate", "signature_problem"]
