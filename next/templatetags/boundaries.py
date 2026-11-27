"""The internal tags composition writes at the slot of a page boundary file.

They install as builtins under a `next:` name and compile to `next.pages.nodes`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

from django import template
from django.template.base import TokenType

from next.pages.nodes import EndNode, FailedNode, LayoutSlotNode, PageBoundaryNode
from next.pages.placeholder import (
    BOUNDARY_END_TAG,
    BOUNDARY_TAG,
    FAILED_TAG,
    NO_WRAPPER,
    SLOT_END_TAG,
    SLOT_TAG,
    is_quoted,
    unquote_source,
)


if TYPE_CHECKING:
    from django.template.base import Parser, Token


register = template.Library()

_ERROR_ARGS: Final = 3
_DEFERRED_ARGS: Final = 4
_EXTENDS: Final = "extends"
_OPENINGS: Final = frozenset({BOUNDARY_TAG, SLOT_TAG})
_SLOT_ARGS: Final = 2
_SLOT_FORM: Final = f"{{% {SLOT_TAG} %}} takes the id of one layout"
_PASSED: Final = (TokenType.TEXT, TokenType.COMMENT)
_BOUNDARY_FORM: Final = (
    f"{{% {BOUNDARY_TAG} %}} takes a quoted error source, a wrapper element and an "
    "optional quoted loading source"
)


def _opens_with_extends(parser: Parser) -> bool:
    """Return whether the content ahead opens with `{% extends %}`.

    Opening boundary and slot tags are looked past, as each holds the same content.
    """
    for token in reversed(cast("list[Token]", parser.tokens)):
        if token.token_type in _PASSED:
            continue
        if token.token_type is not TokenType.BLOCK:
            return False
        command = token.contents.split(maxsplit=1)[:1]
        if not command or command[0] not in _OPENINGS:
            return command == [_EXTENDS]
    return False


@register.tag(name=SLOT_TAG)
def do_slot(parser: Parser, token: Token) -> LayoutSlotNode:
    """Compile the slot tag composition writes around what one layout wraps.

    `{% extends %}` takes the rest of the template, so a content it opens does too.
    """
    bits = token.split_contents()
    if len(bits) != _SLOT_ARGS:
        raise template.TemplateSyntaxError(_SLOT_FORM)
    if _opens_with_extends(parser):
        return LayoutSlotNode(bits[1], parser.parse())
    nodelist = parser.parse((SLOT_END_TAG,))
    parser.delete_first_token()
    return LayoutSlotNode(bits[1], nodelist)


@register.tag(name=SLOT_END_TAG)
def do_slot_end(_parser: Parser, _token: Token) -> EndNode:
    """Compile a closing slot tag that a `{% extends %}` content took in."""
    return EndNode()


@register.tag(name=BOUNDARY_TAG)
def do_boundary(parser: Parser, token: Token) -> PageBoundaryNode:
    """Compile the boundary tag composition writes around the content of one slot.

    `{% extends %}` takes the rest of the template, so a content it opens does too.
    """
    bits = token.split_contents()
    if len(bits) not in {_ERROR_ARGS, _DEFERRED_ARGS} or not all(
        is_quoted(bit) for bit in (bits[1], *bits[3:])
    ):
        raise template.TemplateSyntaxError(_BOUNDARY_FORM)
    source = unquote_source(bits[1])
    loading = unquote_source(bits[3]) if len(bits) == _DEFERRED_ARGS else None
    if source is None and loading is None:
        raise template.TemplateSyntaxError(_BOUNDARY_FORM)
    tag = None if bits[2] == NO_WRAPPER else bits[2]
    if _opens_with_extends(parser):
        return PageBoundaryNode(source, tag, parser.parse(), loading)
    nodelist = parser.parse((BOUNDARY_END_TAG,))
    parser.delete_first_token()
    return PageBoundaryNode(source, tag, nodelist, loading)


@register.tag(name=BOUNDARY_END_TAG)
def do_end(_parser: Parser, _token: Token) -> EndNode:
    """Compile a closing boundary tag that a `{% extends %}` content took in."""
    return EndNode()


@register.tag(name=FAILED_TAG)
def do_failed(_parser: Parser, _token: Token) -> FailedNode:
    """Compile the tag that stands for a page body which failed before rendering."""
    return FailedNode()


__all__ = ["register"]
