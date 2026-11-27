"""The nodes composition writes at the slot of a page boundary file, and their render.

A page boundary renders its content in place, behind its `error.djx`, or as a job.
"""

from __future__ import annotations

import functools
from dataclasses import replace
from typing import TYPE_CHECKING, Final, override

from django.conf import settings
from django.template.base import Node, NodeList

from next.boundaries.calls import Addressing, BoundarySite, DeferredCall, stable_key
from next.boundaries.failures import shown_source
from next.boundaries.options import BoundaryOptions, DeferMode
from next.components.boundaries import deferral_sink
from next.utils import child_nodelists

from .boundaries import CAUGHT_KEY, FAILURE_KEY, Caught
from .defer import page_defer
from .gating import GATE_KEY, ShellGate
from .jobs import PageJob, contain_page
from .layouts import SLOT, context_response_id, enclosed


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from django.template.context import Context


_STREAMED: Final = BoundaryOptions(mode=DeferMode.STREAM)


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
            site=BoundarySite(
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


class LayoutSlotNode(Node):
    """Render the slot one layout wraps between the enclosing comments of the router.

    A render without a response id writes the content alone.
    """

    def __init__(self, ident: str, nodelist: NodeList) -> None:
        """Hold the id of the layout and the content of its slot."""
        self.ident = ident
        self.nodelist = nodelist

    @override
    def render(self, context: Context) -> str:
        """Render the content, enclosed when the request carries a response id."""
        html = self.nodelist.render(context)
        response_id = context_response_id(context)
        return (
            html
            if response_id is None
            else enclosed(SLOT, response_id, html, self.ident)
        )


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
    "LayoutSlotNode",
    "PageBoundaryNode",
    "contained_by",
    "enclosing",
    "failed_by",
]
