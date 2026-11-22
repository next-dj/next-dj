"""The internal tags composition writes at the slot of a page boundary file.

They install as builtins under a `next:` name, so no page template loads them.
"""

from __future__ import annotations

import functools
from dataclasses import replace
from typing import TYPE_CHECKING, Final, cast, override

from django import template
from django.conf import settings
from django.template.base import Node, NodeList, TokenType

from next.components.boundaries import deferral_sink
from next.defer import (
    Addressing,
    BoundaryOptions,
    DeferMode,
    DeferredCall,
    Site,
    stable_key,
)
from next.pages.boundaries import CAUGHT_KEY, FAILURE_KEY, Caught, shown_source
from next.pages.defer import page_defer
from next.pages.gating import GATE_KEY, ShellGate
from next.pages.jobs import PageJob, contain_page
from next.pages.layouts import SLOT, context_seal, sealed
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
from next.utils import child_nodelists


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from django.template.base import Parser, Token
    from django.template.context import Context


register = template.Library()

_ERROR_ARGS: Final = 3
_DEFERRED_ARGS: Final = 4
_EXTENDS: Final = "extends"
_OPENINGS: Final = frozenset({BOUNDARY_TAG, SLOT_TAG})
_SLOT_ARGS: Final = 2
_SLOT_FORM: Final = f"{{% {SLOT_TAG} %}} takes the id of one layout"
_PASSED: Final = (TokenType.TEXT, TokenType.COMMENT)
_STREAMED: Final = BoundaryOptions(mode=DeferMode.STREAM)
_BOUNDARY_FORM: Final = (
    f"{{% {BOUNDARY_TAG} %}} takes a quoted error source, a wrapper element and an "
    "optional quoted loading source"
)


class PageBoundaryNode(Node):
    """Render the content below one page boundary directory.

    Its `error.djx` shows in place of a failed content, and its `loading.djx` defers it.
    """

    def __init__(
        self,
        source: Path | None,
        tag: str | None,
        nodelist: NodeList,
        loading: Path | None = None,
    ) -> None:
        """Hold the `error.djx`, the wrapper, the content and the `loading.djx`."""
        self.source = source
        self.tag = tag
        self.nodelist = nodelist
        self.loading = loading
        self.loading_shown = "" if loading is None else shown_source(loading)
        self.key = "" if loading is None else stable_key(self.loading_shown, 0)

    @override
    def render(self, context: Context) -> str:
        """Render the content inside the guarded runner, or defer it to a job.

        A failure a job met for this boundary renders its error UI in place.
        """
        if self.loading is not None:
            return self._deferred(context, self.loading)
        caught = context.get(CAUGHT_KEY)
        if isinstance(caught, Caught) and caught.boundary.source == self.source:
            return failed_by((self,), context, caught.error)
        return self.contain(context, functools.partial(self._content, context))

    def contain(self, context: Context, render: Callable[[], str]) -> str:
        """Return what `render` returns, or the error UI of this boundary when it fails.

        A zone GET renders its body through it, as the page renders the whole content.
        """
        if self.source is None:
            return render()
        name = self.loading_shown or None
        return contain_page(self.source, self.tag, context, render, name=name)

    def _deferred(self, context: Context, loading: Path) -> str:
        """Hand the content to the deferral sink, or render it in place without one."""
        job = PageJob(self.source, self.tag, self.nodelist, loading, self.loading_shown)
        sink = deferral_sink(context)
        if sink is None:
            return self.contain(context, functools.partial(job.content, context))
        origin = getattr(self, "origin", None)
        call = DeferredCall(
            site=Site(
                Addressing.PAGE,
                self.loading_shown,
                DeferMode.STREAM,
                "" if origin is None else str(origin.name),
            ),
            options=_options(context),
            key=self.key,
            tag=self.tag,
            source=self.loading_shown if settings.DEBUG else None,
            renderer=job,
        )
        return sink.queue(context, call)

    def _content(self, context: Context) -> str:
        """Render the content, keeping what it set only once it rendered whole."""
        layer = context.push()
        try:
            html = self.nodelist.render(context)
        finally:
            context.pop()
        context.dicts[-1].update(layer)
        return html


class FailedNode(Node):
    """Raise the failure the page met before its template rendered, for its boundary."""

    @override
    def render(self, context: Context) -> str:
        """Raise the held failure, or render nothing when the context holds none."""
        failure = context.get(FAILURE_KEY)
        if isinstance(failure, Exception):
            raise failure
        return ""


class EndNode(Node):
    """The closing boundary tag a content opening with `{% extends %}` takes in."""

    @override
    def render(self, context: Context) -> str:
        """Render nothing, as `{% extends %}` renders only its blocks anyway."""
        return ""


def _options(context: Context) -> BoundaryOptions:
    """Return the `page.defer` options of the page the gate of `context` renders."""
    gate = context.get(GATE_KEY)
    if not isinstance(gate, ShellGate):
        return _STREAMED
    declared = page_defer(gate.file_path)
    return _STREAMED if declared is None else replace(declared, mode=DeferMode.STREAM)


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


class SlotNode(Node):
    """Render the slot one layout wraps between the sealed comments of the router.

    A render without a router seal writes the content alone.
    """

    def __init__(self, ident: str, nodelist: NodeList) -> None:
        """Hold the id of the layout and the content of its slot."""
        self.ident = ident
        self.nodelist = nodelist

    @override
    def render(self, context: Context) -> str:
        """Render the content, sealed when the request carries a router seal."""
        html = self.nodelist.render(context)
        seal = context_seal(context)
        return html if seal is None else sealed(SLOT, seal, html, self.ident)


@register.tag(name=SLOT_TAG)
def do_slot(parser: Parser, token: Token) -> SlotNode:
    """Compile the slot tag composition writes around what one layout wraps.

    `{% extends %}` takes the rest of the template, so a content it opens does too.
    """
    bits = token.split_contents()
    if len(bits) != _SLOT_ARGS:
        raise template.TemplateSyntaxError(_SLOT_FORM)
    if _opens_with_extends(parser):
        return SlotNode(bits[1], parser.parse())
    nodelist = parser.parse((SLOT_END_TAG,))
    parser.delete_first_token()
    return SlotNode(bits[1], nodelist)


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


def enclosing(
    nodelist: NodeList, target: NodeList
) -> tuple[PageBoundaryNode, ...] | None:
    """Return the page boundaries around `target` in `nodelist`, innermost first.

    `None` means `target` is not in `nodelist` at all.
    """
    if nodelist is target:
        return ()
    for node in nodelist:
        for child in child_nodelists(node):
            found = enclosing(child, target)
            if found is not None:
                return (*found, node) if isinstance(node, PageBoundaryNode) else found
    return None


def contained_by(
    nodes: tuple[PageBoundaryNode, ...], context: Context, render: Callable[[], str]
) -> str:
    """Return what `render` returns inside `nodes`, the innermost catching first."""
    for node in nodes:
        render = functools.partial(node.contain, context, render)
    return render()


def _raise(error: Exception) -> str:
    raise error


def failed_by(
    nodes: tuple[PageBoundaryNode, ...], context: Context, error: Exception
) -> str:
    """Return the error UI the innermost of `nodes` holding `error` renders for it."""
    return contained_by(nodes, context, functools.partial(_raise, error))


__all__ = [
    "EndNode",
    "FailedNode",
    "PageBoundaryNode",
    "SlotNode",
    "contained_by",
    "enclosing",
    "failed_by",
    "register",
]
