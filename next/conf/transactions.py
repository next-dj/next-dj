"""The shape of the `TRANSACTIONS` scope and the predicates its values pass."""

from typing import Final, Literal, TypeGuard

from .defaults import AUTO
from .shapes import Section, Shape, alternatives, choice


SCOPE: Final = "TRANSACTIONS"

ALL_ALIASES: Final = "all"
"""The `ALIASES` value that names every database alias Django holds a connection for."""

ATOMIC: Final = choice(("writes", True, False))
"""The shape of an atomic mode, as `TRANSACTIONS` and `BOUNDARIES["ATOMIC"]` take it."""

_METHOD_MODES: Final = alternatives((True, False, "writes", AUTO))


def atomic_choice(value: object) -> TypeGuard[bool | Literal["writes"]]:
    """Whether `value` is an atomic mode, `True`, `False` or `"writes"`."""
    return ATOMIC.usable(value)


def method_choice(value: object) -> TypeGuard[bool | Literal["writes", "auto"]]:
    """Whether `value` is a `SAFE` or `UNSAFE` mode, `"auto"` following Django."""
    return (isinstance(value, str) and value == AUTO) or atomic_choice(value)


def alias_list(value: object) -> TypeGuard[list[str] | tuple[str, ...]]:
    """Whether `value` is a list or a tuple of alias names."""
    return isinstance(value, list | tuple) and all(isinstance(n, str) for n in value)


def aliases_usable(
    value: object,
) -> TypeGuard[Literal["all"] | list[str] | tuple[str, ...]]:
    """Whether `value` is a usable `ALIASES` value."""
    return value == ALL_ALIASES or alias_list(value)


def rule_usable(value: object) -> TypeGuard[str | None]:
    """Whether `value` is a usable `RULE` value, before its path is imported."""
    return value is None or isinstance(value, str)


SHAPE: Final = Section(
    {
        "SAFE": Shape(method_choice, _METHOD_MODES),
        "UNSAFE": Shape(method_choice, _METHOD_MODES),
        "ALIASES": Shape(aliases_usable, "'all' or a list of database aliases"),
        "RULE": Shape(rule_usable, "the dotted path of a callable or None"),
    }
)
"""Every `TRANSACTIONS` key with what it accepts, read by the checks and the policy."""


__all__ = [
    "ALL_ALIASES",
    "ATOMIC",
    "SCOPE",
    "SHAPE",
    "alias_list",
    "aliases_usable",
    "atomic_choice",
    "method_choice",
    "rule_usable",
]
