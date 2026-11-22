"""How a page boundary renders its error UI, and how a deferred one runs as a job.

It reads the static manager, so nothing the pages package imports may import it.
"""

import functools
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Final, cast, override

from django import template
from django.template.base import NodeList
from django.template.context import Context

from next.boundaries import (
    Boundary,
    Fallback,
    claim_wrapper,
    contain,
    contain_bare,
    guarded,
    protect,
)
from next.components.fallback import retry_loads, unrun_error
from next.components.stash import loading_content
from next.defer import DEFERRALS_KEY, Addressing, BoundaryFailure, ErrorKind
from next.permissions.nodes import rendered_under, rendering_guards
from next.seeding import COLLECTOR_KEY, REQUEST_KEY, TEMPLATE_PATH_KEY
from next.static.collector import StaticCollector

from .boundaries import (
    CAUGHT_KEY,
    SHELL_KEY,
    Caught,
    ErrorShell,
    boundary_template,
    catching,
    mark_failed,
    shown_source,
)
from .gating import GATE_KEY, Advanced, ShellGate
from .graph import Blame, blaming


_LIVE_KEYS: Final = (REQUEST_KEY, COLLECTOR_KEY)
_JOB_KEYS: Final = (*_LIVE_KEYS, DEFERRALS_KEY)


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


class PageFallback(Fallback):
    """The error UI of one page boundary, rendered from the shell of its directory."""

    __slots__ = ("_context", "_source", "_tag")

    retry = staticmethod(retry_loads)

    def __init__(self, source: Path, tag: str | None, context: Context) -> None:
        """Hold the `error.djx`, the wrapper of its slot and the context it reads."""
        self._source = source
        self._tag = tag
        self._context = context

    @override
    def retry_token(self, error: BoundaryFailure) -> str:
        """Return no token, as no props of a page boundary ride a retry."""
        return ""

    @override
    def tag(self) -> str | None:
        """Return the wrapper element composition chose for the slot."""
        return self._tag

    @override
    def render(self, error: BoundaryFailure, kind: ErrorKind) -> str:
        """Render `error.djx` from the shell and `error`, the page failing.

        Nothing the failed content or a callable below the cut produced reaches it.
        """
        mark_failed(kind)
        source = self._source
        context = self._context
        held = context.get(SHELL_KEY)
        shell = held.of(source) if isinstance(held, ErrorShell) else {}
        for key in _LIVE_KEYS:
            if key in context:
                shell[key] = context[key]
        shell[TEMPLATE_PATH_KEY] = str(source)
        shell["error"] = error
        return _rendered(source, shell, context)


def page_boundary(
    source: Path, context: Context, *, name: str | None = None, ident: str | None = None
) -> Boundary:
    """Return the error boundary of the `error.djx` at `source`, as `context` meets it.

    A boundary with a `loading.djx` is named by that file, else by its `error.djx`.
    """
    shown = shown_source(source)
    return Boundary(
        shown if name is None else name,
        Addressing.PAGE,
        request=context.get(REQUEST_KEY),
        collector=context.get(COLLECTOR_KEY),
        source=shown,
        ident=ident,
    )


def contain_page(
    source: Path,
    tag: str | None,
    context: Context,
    render: Callable[[], str],
    *,
    name: str | None = None,
) -> str:
    """Return what `render` returns, or the error UI of `source` in its wrapper."""
    return contain(
        page_boundary(source, context, name=name),
        render,
        PageFallback(source, tag, context),
    )


class PageJob:
    """How one deferred page boundary renders its loading UI, its content and errors.

    A failure is contained by the `error.djx` of its directory, else the nearest above.
    """

    __slots__ = ("_guards", "_loading", "_name", "_nodelist", "_source", "_tag")

    def __init__(
        self,
        source: Path | None,
        tag: str | None,
        nodelist: NodeList,
        loading: Path,
        name: str,
    ) -> None:
        """Hold the boundary, its loading and name, and the branches open now."""
        self._source = source
        self._tag = tag
        self._nodelist = nodelist
        self._loading = loading
        self._name = name
        self._guards = rendering_guards()

    def _guard(self, context: Context) -> Path | None:
        """Return the `error.djx` that contains the job, `None` when none does."""
        if self._source is not None:
            return self._source
        gate = context.get(GATE_KEY)
        if not isinstance(gate, ShellGate):
            return None
        level = gate.level(self._loading)
        guard = None if level is None else gate.chain.guard_of(level)
        return None if guard is None else guard.source

    def _bare(
        self, source: Path | None, context: Context, render: Callable[[], str]
    ) -> tuple[str, BoundaryFailure | None]:
        """Run `render` inside the boundary of `source`, the error UI unwrapped.

        Its boundary claims the id of the wrapper around it, which a failure names.
        """
        ident = claim_wrapper()
        with rendered_under(self._guards):
            if source is None:
                return render(), None
            return contain_bare(
                page_boundary(source, context, name=self._name, ident=ident),
                render,
                PageFallback(source, self._tag, context),
            )

    def loading(self, context: Context) -> tuple[str, BoundaryFailure | None]:
        """Render the `loading.djx` over the shell context, its own failure settled."""
        loading = self._loading
        data = cast("dict[str, object]", context.flatten())
        data[TEMPLATE_PATH_KEY] = str(loading)
        render = functools.partial(_loading_rendered, loading, data, context)
        return self._bare(self._source, context, render)

    def content(self, context: Context) -> str:
        """Run the gated callables of the boundary, and render the content over them.

        A failure an `error.djx` inside the content holds renders there, as inline.
        """
        gate = context.get(GATE_KEY)
        if not isinstance(gate, ShellGate):
            return self._nodelist.render(context)
        work = functools.partial(gate.advance, self._loading)
        with blaming() as blamed:
            outcome = guarded(protect(), work, self._held, context, gate, blamed)
        if not isinstance(outcome, Advanced):
            return self._failed(context, *outcome)
        collector = context.get(COLLECTOR_KEY)
        if isinstance(collector, StaticCollector):
            serializers = outcome.serializers
            for key, value in outcome.published.items():
                collector.add_js_context(key, value, serializer=serializers.get(key))
        layer = outcome.layer
        held = context.get(SHELL_KEY)
        following = layer.get(GATE_KEY)
        if isinstance(held, ErrorShell) and isinstance(following, ShellGate):
            layer[SHELL_KEY] = replace(held, blame=following.blame())
        with context.push(layer):
            return self._nodelist.render(context)

    def _held(
        self, exc: Exception, context: Context, gate: ShellGate, blamed: Blame
    ) -> tuple[Caught, ErrorShell] | None:
        """Return the boundary inside the content that holds `exc`, with its shell.

        `None` leaves `exc` to a boundary at or above this one's directory.
        """
        guard = catching(gate.chain, exc, gate.plan.failed_level(blamed))
        held = context.get(SHELL_KEY)
        if (
            guard is None
            or not gate.inside(self._loading, guard.level)
            or not isinstance(held, ErrorShell)
        ):
            return None
        return Caught(guard, exc), replace(held, blame=gate.blame(blamed))

    def _failed(self, context: Context, caught: Caught, shell: ErrorShell) -> str:
        """Render the content over the shell of `caught`, its boundary failing."""
        data = shell.at(caught.boundary.level)
        for key in _JOB_KEYS:
            if key in context:
                data[key] = context[key]
        data[CAUGHT_KEY] = caught
        return self._nodelist.render(context.new(data))

    def body(self, context: Context) -> tuple[str, BoundaryFailure | None]:
        """Render the content inside its error boundary, the error UI unwrapped."""
        render = functools.partial(self.content, context)
        return self._bare(self._guard(context), context, render)

    def error(self, context: Context, kind: ErrorKind) -> tuple[str, BoundaryFailure]:
        """Render the error UI of `kind` without running the content."""
        error = unrun_error(kind)
        guard = self._guard(context)
        if guard is None:
            mark_failed(kind)
            return "", error
        return PageFallback(guard, self._tag, context).render(error, kind), error


__all__ = ["PageFallback", "PageJob", "contain_page", "page_boundary"]
