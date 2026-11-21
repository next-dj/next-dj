"""The internal tags composition writes at the slot of a page boundary file.

They install as builtins under a `next:` name, so no page template loads or spells them.
"""

from __future__ import annotations

import functools
from dataclasses import replace
from typing import TYPE_CHECKING, Final, cast, override

from django import template
from django.conf import settings
from django.template.base import Node, NodeList, TokenType

from next.boundaries import Boundary, claim_wrapper, contain, protect
from next.components.boundary import deferral_sink
from next.components.fallback import retry_loads, unrun_error
from next.components.stash import loading_content
from next.defer import (
    DEFERRALS_KEY,
    Addressing,
    BoundaryError,
    BoundaryOptions,
    DeferMode,
    DeferredCall,
    Site,
    stable_key,
)
from next.pages.boundaries import (
    ADDRESSING,
    CAUGHT_KEY,
    FAILURE_KEY,
    SHELL_KEY,
    Caught,
    Shell,
    boundary_template,
    catching,
    mark_failed,
    shown_source,
)
from next.pages.deferral import page_defer
from next.pages.gating import GATE_KEY, Gate
from next.pages.graph import blaming
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
from next.permissions.nodes import rendered_under, rendering_guards
from next.seeding import COLLECTOR_KEY, REQUEST_KEY, TEMPLATE_PATH_KEY
from next.static.collector import StaticCollector
from next.utils import child_nodelists


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from django.template.base import Parser, Token
    from django.template.context import Context

    from next.defer import ErrorKind
    from next.pages.graph import Blame


register = template.Library()

_ERROR_ARGS: Final = 3
_DEFERRED_ARGS: Final = 4
_EXTENDS: Final = "extends"
_OPENINGS: Final = frozenset({BOUNDARY_TAG, SLOT_TAG})
_SLOT_ARGS: Final = 2
_SLOT_FORM: Final = f"{{% {SLOT_TAG} %}} takes the id of one layout"
_PASSED: Final = (TokenType.TEXT, TokenType.COMMENT)
_LIVE_KEYS: Final = (REQUEST_KEY, COLLECTOR_KEY)
_JOB_KEYS: Final = (*_LIVE_KEYS, DEFERRALS_KEY)
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
            return self.contain(context, functools.partial(_raise, caught.error))
        return self.contain(context, functools.partial(self._content, context))

    def contain(self, context: Context, render: Callable[[], str]) -> str:
        """Return what `render` returns, or the error UI of this boundary when it fails.

        A zone GET renders its body through it, as the page renders the whole content.
        """
        if self.source is None:
            return render()
        return _contained(self.source, self.tag, context, render)[0]

    def _deferred(self, context: Context, loading: Path) -> str:
        """Hand the content to the deferral sink, or render it in place without one."""
        job = PageJob(self, loading)
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


def _raise(error: Exception) -> str:
    raise error


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
    if not isinstance(gate, Gate):
        return _STREAMED
    declared = page_defer(gate.file_path)
    return _STREAMED if declared is None else replace(declared, mode=DeferMode.STREAM)


def _rendered(source: Path, data: dict[str, object], context: Context) -> str:
    """Render the boundary file at `source` over `data`, as `context` renders."""
    return boundary_template(source).render(
        template.Context(
            data,
            autoescape=context.autoescape,
            use_l10n=context.use_l10n,
            use_tz=context.use_tz,
        )
    )


def _loading_rendered(source: Path, data: dict[str, object], context: Context) -> str:
    """Render the `loading.djx` at `source`, deferred components inside in place."""
    with loading_content():
        return _rendered(source, data, context)


class _PageFallback:
    """The error UI of one page boundary, rendered from the shell of its directory.

    `settled` keeps the error UI and its error, which a deferred boundary wraps itself.
    """

    __slots__ = ("_context", "_source", "_tag", "settled")

    def __init__(self, source: Path, tag: str | None, context: Context) -> None:
        """Hold the `error.djx`, the wrapper of its slot and the context it reads."""
        self._source = source
        self._tag = tag
        self._context = context
        self.settled: tuple[str, BoundaryError] | None = None

    def retry(self) -> bool:
        """Return whether the runtime script loads, which every retry needs."""
        return retry_loads()

    def retry_token(self, error: BoundaryError) -> str:
        """Return no token, as no props of a page boundary ride a retry."""
        del error
        return ""

    def tag(self) -> str | None:
        """Return the wrapper element composition chose for the slot."""
        return self._tag

    def render(self, error: BoundaryError, kind: ErrorKind) -> str:
        """Render `error.djx` from the shell and `error`, the page failing.

        Nothing the failed content or a callable below the cut produced reaches it.
        """
        mark_failed(kind)
        source = self._source
        context = self._context
        held = context.get(SHELL_KEY)
        shell = held.of(source) if isinstance(held, Shell) else {}
        for key in _LIVE_KEYS:
            if key in context:
                shell[key] = context[key]
        shell[TEMPLATE_PATH_KEY] = str(source)
        shell["error"] = error
        html = _rendered(source, shell, context)
        self.settled = (html, error)
        return html


def _contained(
    source: Path,
    tag: str | None,
    context: Context,
    render: Callable[[], str],
    ident: str | None = None,
) -> tuple[str, tuple[str, BoundaryError] | None]:
    """Run `render` inside the error boundary of `source`, with the error UI shown.

    `ident` names the wrapper a deferred boundary is drawn in, which a failure reports.
    """
    boundary = Boundary(
        str(source),
        ADDRESSING,
        request=context.get(REQUEST_KEY),
        collector=context.get(COLLECTOR_KEY),
        source=shown_source(source),
        ident=ident,
    )
    fallback = _PageFallback(source, tag, context)
    html = contain(boundary, render, fallback)
    return html, fallback.settled


class PageJob:
    """How one deferred page boundary renders its loading UI, its content and errors.

    A failure is contained by the `error.djx` of its directory, else the nearest above.
    """

    __slots__ = ("_guards", "_loading", "_node")

    def __init__(self, node: PageBoundaryNode, loading: Path) -> None:
        """Hold the boundary node, its loading and the granting branches open now."""
        self._node = node
        self._loading = loading
        self._guards = rendering_guards()

    def _guard(self, context: Context) -> Path | None:
        """Return the `error.djx` that contains the job, `None` when none does."""
        node = self._node
        if node.source is not None:
            return node.source
        gate = context.get(GATE_KEY)
        if not isinstance(gate, Gate):
            return None
        level = gate.level(self._loading)
        guard = None if level is None else gate.chain.guard_of(level)
        return None if guard is None else guard.source

    def loading(self, context: Context) -> tuple[str, BoundaryError | None]:
        """Render the `loading.djx` over the shell context, its own failure settled."""
        node = self._node
        loading = self._loading
        data = cast("dict[str, object]", context.flatten())
        data[TEMPLATE_PATH_KEY] = str(loading)
        render = functools.partial(_loading_rendered, loading, data, context)
        ident = claim_wrapper()
        with rendered_under(self._guards):
            if node.source is None:
                return render(), None
            html, settled = _contained(node.source, node.tag, context, render, ident)
        return (html, None) if settled is None else settled

    def content(self, context: Context) -> str:
        """Run the gated callables of the boundary, and render the content over them.

        A failure an `error.djx` inside the content holds renders there, as inline.
        """
        gate = context.get(GATE_KEY)
        if not isinstance(gate, Gate):
            return self._node.nodelist.render(context)
        protection = protect()
        with blaming() as blamed:
            try:
                advanced = gate.advance(self._loading)
            except Exception as exc:
                undone = protection.undo()
                held = self._held(context, gate, exc, blamed) if undone else None
                if held is None:
                    raise
                return self._failed(context, *held)
            except BaseException:
                protection.undo()
                raise
        protection.keep()
        collector = context.get(COLLECTOR_KEY)
        if isinstance(collector, StaticCollector):
            serializers = advanced.serializers
            for key, value in advanced.published.items():
                collector.add_js_context(key, value, serializer=serializers.get(key))
        layer = advanced.layer
        held = context.get(SHELL_KEY)
        following = layer.get(GATE_KEY)
        if isinstance(held, Shell) and isinstance(following, Gate):
            layer[SHELL_KEY] = replace(held, blame=following.blame())
        with context.push(layer):
            return self._node.nodelist.render(context)

    def _held(
        self, context: Context, gate: Gate, exc: Exception, blamed: Blame
    ) -> tuple[Caught, Shell] | None:
        """Return the boundary inside the content that holds `exc`, with its shell.

        `None` leaves `exc` to a boundary at or above this one's directory.
        """
        guard = catching(gate.chain, exc, gate.plan.failed_level(blamed))
        held = context.get(SHELL_KEY)
        if (
            guard is None
            or not gate.inside(self._loading, guard.level)
            or not isinstance(held, Shell)
        ):
            return None
        return Caught(guard, exc), replace(held, blame=gate.blame(blamed))

    def _failed(self, context: Context, caught: Caught, shell: Shell) -> str:
        """Render the content over the shell of `caught`, its boundary failing."""
        data = shell.at(caught.boundary.level)
        for key in _JOB_KEYS:
            if key in context:
                data[key] = context[key]
        data[CAUGHT_KEY] = caught
        return self._node.nodelist.render(context.new(data))

    def body(self, context: Context) -> tuple[str, BoundaryError | None]:
        """Render the content inside its error boundary, the error UI unwrapped."""
        guard = self._guard(context)
        render = functools.partial(self.content, context)
        ident = claim_wrapper()
        with rendered_under(self._guards):
            if guard is None:
                return render(), None
            html, settled = _contained(guard, self._node.tag, context, render, ident)
        return (html, None) if settled is None else settled

    def error(self, context: Context, kind: ErrorKind) -> tuple[str, BoundaryError]:
        """Render the error UI of `kind` without running the content."""
        error = unrun_error(kind)
        guard = self._guard(context)
        if guard is None:
            mark_failed(kind)
            return "", error
        fallback = _PageFallback(guard, self._node.tag, context)
        return fallback.render(error, kind), error


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


__all__ = [
    "EndNode",
    "FailedNode",
    "PageBoundaryNode",
    "PageJob",
    "SlotNode",
    "contained_by",
    "enclosing",
    "register",
]
