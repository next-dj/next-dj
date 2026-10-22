"""Interpolation of `{{ expr }}` inside the string a template tag argument holds."""

import functools
import re

from django import template
from django.template.base import (
    FilterExpression,
    Parser,
    Variable,
    render_value_in_context,
)
from django.template.defaultfilters import register as default_filters


_INTERPOLATION = re.compile(r"\{\{(.*?)\}\}")

_COMPILE_MEMO_MAX_ENTRIES = 1024


class Interpolation:
    """A string whose `{{ expr }}` parts resolve against a template context.

    A lone expression hands over its value as it is, so a prop keeps its type. Text
    around expressions joins them into a plain string that the output autoescapes.
    """

    __slots__ = ("parts",)

    def __init__(self, parts: list[str | FilterExpression]) -> None:
        """Store the literal text and the compiled expressions in source order."""
        self.parts = parts

    def resolve(self, context: template.Context) -> object:
        """Return the lone expression value, else the parts joined as one string."""
        if len(self.parts) == 1 and isinstance(self.parts[0], FilterExpression):
            return self.parts[0].resolve(context)
        return "".join(
            part if isinstance(part, str) else _display(part.resolve(context), context)
            for part in self.parts
        )


def _display(value: object, context: template.Context) -> str:
    """Return `value` as a `{{ }}` prints it, localised and left unescaped."""
    plain = template.Context(
        autoescape=False, use_l10n=context.use_l10n, use_tz=context.use_tz
    )
    return render_value_in_context(value, plain)


def _compile(text: str, parser: Parser) -> Interpolation:
    """Split `text` into literal runs and compiled `{{ }}` expressions."""
    parts: list[str | FilterExpression] = []
    position = 0
    for match in _INTERPOLATION.finditer(text):
        if match.start() > position:
            parts.append(text[position : match.start()])
        source = match.group(1).strip()
        if not source:
            msg = "Empty {{ }} in a template tag argument"
            raise template.TemplateSyntaxError(msg)
        parts.append(parser.compile_filter(source))
        position = match.end()
    if position < len(text):
        parts.append(text[position:])
    return Interpolation(parts)


def interpolated(parser: Parser, expr: FilterExpression) -> Interpolation | None:
    """Return the interpolation a bare string literal carries, else None.

    A literal with a filter keeps Django's meaning, and so does one with no `{{`.
    """
    literal = expr.var
    if isinstance(literal, Variable) or expr.filters or "{{" not in literal:
        return None
    return _compile(literal, parser)


@functools.cache
def _blank_template() -> template.Template:
    """Return the empty template a scope binds, so a missing name reads as invalid."""
    return template.Template("")


def scoped(data: dict[str, object]) -> template.Context:
    """Return a context over `data` that resolves outside any rendering template."""
    context = template.Context(data)
    context.template = _blank_template()
    return context


@functools.lru_cache(maxsize=_COMPILE_MEMO_MAX_ENTRIES)
def interpolation_of(text: str) -> Interpolation | None:
    """Return the interpolation of a plain string outside any template, else None.

    The expressions compile with the default filters, and the result is memoised.
    """
    if "{{" not in text:
        return None
    return _compile(text, Parser([], builtins=[default_filters]))


__all__ = ["Interpolation", "interpolated", "interpolation_of", "scoped"]
