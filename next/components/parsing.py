"""How a `{% component %}` call and its slot tags parse their arguments."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Final
from weakref import WeakKeyDictionary

from django import template
from django.template.base import (
    FilterExpression,
    NodeList,
    Parser,
    Token,
    Variable,
    token_kwargs,
)

from next.defer import BoundaryOptionError, BoundaryOptions
from next.interpolation import Interpolation, interpolated
from next.permissions.nodes import granted_perms

from .registry import may_defer


if TYPE_CHECKING:
    from collections.abc import Iterator


_SLOT_ARG_COUNT = 2

END_BLOCK_COMPONENT = ("/component",)
_END_BLOCK_SLOT = ("/slot",)
_END_BLOCK_SET_SLOT = ("/set_slot",)


class Absent(Enum):
    """An absent slot key or a prop that reads the context, told apart from a value."""

    SLOT = auto()
    LITERAL = auto()


_OPTION_KEYS: Final = ("defer", "timeout")
_ZONE_ONLY_KEYS: Final = ("reload", "delay")
_CAPTURING_TAGS: Final = frozenset({"cache"})

_sites: WeakKeyDictionary[Parser, Iterator[int]] = WeakKeyDictionary()


def _strip_quotes(raw: str) -> str:
    return raw.strip("'\"").strip()


def _site_index(parser: Parser) -> int:
    """Return the position of the next component call among those of one template."""
    counter = _sites.get(parser)
    if counter is None:
        counter = _sites[parser] = itertools.count()
    return next(counter)


def _literal(expr: FilterExpression | Interpolation) -> object:
    """Return the literal `expr` writes, `Absent.LITERAL` when it reads the context."""
    if isinstance(expr, Interpolation) or expr.filters:
        return Absent.LITERAL
    var = expr.var
    if isinstance(var, Variable):
        return Absent.LITERAL if var.literal is None else var.literal
    return str.__str__(var)


_CONSTANTS: Final[dict[tuple[str, ...], object]] = {
    ("True",): True,
    ("False",): False,
    ("None",): None,
}


def constant(expr: FilterExpression | Interpolation) -> object:
    """Return the value `expr` renders in any context, `Absent.LITERAL` for none.

    `True`, `False` and `None` count, as every template context holds them.
    """
    if isinstance(expr, FilterExpression) and not expr.filters:
        var = expr.var
        if isinstance(var, Variable) and var.literal is None:
            return _CONSTANTS.get(var.lookups or (), Absent.LITERAL)
    return _literal(expr)


def _parse_options(
    tag: str, props: dict[str, FilterExpression | Interpolation]
) -> BoundaryOptions | None:
    """Take the boundary options out of `props`, `None` when the call writes none.

    `reload=` and `delay=` stay reserved, so a call that writes either is refused.
    """
    for key in _ZONE_ONLY_KEYS:
        if key in props:
            msg = f"{tag} tag reserves {key}=, an option of {{% zone %}} only"
            raise template.TemplateSyntaxError(msg)
    written = {key: props.pop(key) for key in _OPTION_KEYS if key in props}
    if not written:
        return None
    values: dict[str, object] = {}
    for key, expr in written.items():
        value = _literal(expr)
        if value is Absent.LITERAL:
            msg = f"{tag} tag takes {key}= as a literal, not a template variable"
            raise template.TemplateSyntaxError(msg)
        values[key] = value
    try:
        options = BoundaryOptions.parse(
            values.get("defer"), timeout=values.get("timeout")
        )
    except BoundaryOptionError as error:
        msg = f"{tag} tag refuses its boundary options, {error}"
        raise template.TemplateSyntaxError(msg) from error
    if options.mode is not None:
        may_defer()
    return options


@dataclass(frozen=True, slots=True)
class Call:
    """What one component tag compiles to before its body is parsed."""

    name: str
    props: dict[str, FilterExpression | Interpolation]
    options: BoundaryOptions | None
    index: int
    captured: bool
    perms: tuple[str, ...] | None = ()


def parse_component(parser: Parser, token: Token, tag: str) -> Call:
    """Parse ``tag "name" key=expr …`` into the component name, props and options.

    Django reads the props, so each value compiles to a render-time `FilterExpression`.
    """
    bits = token.split_contents()[1:]
    if not bits:
        msg = f"{tag} tag requires at least a component name"
        raise template.TemplateSyntaxError(msg)
    name = _strip_quotes(bits.pop(0))
    if not name:
        msg = f"{tag} tag requires a quoted component name"
        raise template.TemplateSyntaxError(msg)
    props = _parse_props(parser, bits)
    options = _parse_options(tag, props)
    stack = parser.command_stack
    captured = any(command in _CAPTURING_TAGS for command, _ in stack)
    perms = granted_perms(parser)
    return Call(name, props, options, _site_index(parser), captured, perms)


def _parse_props(
    parser: Parser, bits: list[str]
) -> dict[str, FilterExpression | Interpolation]:
    """Parse the `key=expr` props a component tag carries, consuming `bits`."""
    props: dict[str, FilterExpression | Interpolation] = {}
    while bits:
        for key, expr in token_kwargs(bits, parser).items():
            props[key] = interpolated(parser, expr) or expr
        if bits:
            # Django stops at the first non-pair bit, an unmeant word, so it is dropped.
            del bits[0]
    return props


@dataclass(frozen=True, slots=True)
class NamedTagSpec:
    """What one named slot tag needs to compile, in its paired or its short form."""

    empty_name_message: str
    wrong_arity_message: str
    end_tokens: tuple[str, ...] = ()


def parse_named_tag(
    parser: Parser, token: Token, spec: NamedTagSpec
) -> tuple[str, NodeList]:
    """Parse ``tag "name"`` into a name and the body only the paired form carries."""
    bits = token.split_contents()
    if len(bits) != _SLOT_ARG_COUNT:
        raise template.TemplateSyntaxError(spec.wrong_arity_message)
    name = _strip_quotes(bits[1])
    if not name:
        raise template.TemplateSyntaxError(spec.empty_name_message)
    if not spec.end_tokens:
        return name, NodeList()
    nodelist = parser.parse(spec.end_tokens)
    parser.delete_first_token()
    return name, nodelist


BLOCK_SLOT_SPEC = NamedTagSpec(
    empty_name_message="{% #slot %} tag requires a quoted slot name",
    wrong_arity_message="{% #slot %} tag requires exactly one argument: slot name",
    end_tokens=_END_BLOCK_SLOT,
)

SHORT_SLOT_SPEC = NamedTagSpec(
    empty_name_message="{% slot %} tag requires a quoted slot name",
    wrong_arity_message="{% slot %} short form requires exactly one quoted slot name",
)

SET_SLOT_SPEC = NamedTagSpec(
    empty_name_message="{% #set_slot %} tag requires a quoted slot name",
    wrong_arity_message="{% #set_slot %} tag requires exactly one argument: slot name",
    end_tokens=_END_BLOCK_SET_SLOT,
)

SHORT_SET_SLOT_SPEC = NamedTagSpec(
    empty_name_message="{% set_slot %} tag requires a quoted slot name",
    wrong_arity_message=(
        "{% set_slot %} short form requires exactly one quoted slot name"
    ),
)


__all__ = [
    "BLOCK_SLOT_SPEC",
    "END_BLOCK_COMPONENT",
    "SET_SLOT_SPEC",
    "SHORT_SET_SLOT_SPEC",
    "SHORT_SLOT_SPEC",
    "Absent",
    "Call",
    "NamedTagSpec",
    "constant",
    "parse_component",
    "parse_named_tag",
]
