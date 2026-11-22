"""How one guarded or deferred component call renders behind its error boundary.

It reads the static manager, so nothing the components package imports may import it.
"""

import functools
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, Protocol, override

from django.conf import settings
from django.template import Context

from next.boundaries import (
    Boundary,
    Fallback,
    claim_wrapper,
    contain,
    contain_bare,
    misrooted,
    new_digest,
)
from next.defer import BoundaryFailure, DeferMode, ErrorKind
from next.diagnostics import mark_degraded
from next.pages.boundaries import shown_source
from next.permissions.nodes import Guards, rendered_under, rendering_guards
from next.seeding import REQUEST_KEY

from .boundaries import UNDISCLOSED, Attempt, current_attempt, deferral_sink, ruled
from .facade import render_component_error, render_component_loading
from .fallback import retry_loads, unrun_error
from .info import ComponentInfo
from .stash import loading_content
from .trip import TripInput, addressable, client_mode


logger = logging.getLogger(__name__)

_MISROOTED: Final = (
    "%s renders %s where its boundary adopts the root element, so it settles as an "
    "error."
)
_MISROOTED_LOG: Final = _MISROOTED[:-1] + ", digest %s."


class CallSite(Protocol):
    """The compiled call of a component its error UI and its deferred render read."""

    @property
    def site(self) -> str:
        """Return the template and the line of the call, which messages name."""

    def attempt(
        self,
        context: Context,
        info: ComponentInfo,
        path: Path,
        attempt: Attempt,
        props: dict[str, Any] | None = None,
    ) -> str:
        """Render the subtree of the call, its assets and props noted on `attempt`."""

    def guard(
        self,
        context: Context,
        info: ComponentInfo,
        source: str | None,
        ident: str | None = None,
    ) -> Boundary:
        """Return the error boundary of the call, `ident` the wrapper around it."""

    def wrapper(self, context: Context, info: ComponentInfo) -> str | None:
        """Return the wrapper element the call allows, `None` for an adopted root."""

    def file_context(self, context: Context, path: Path) -> dict[str, Any]:
        """Return the call-site context a `loading.djx` or an `error.djx` reads."""

    def token_input(
        self, info: ComponentInfo, mode: DeferMode, props: dict[str, Any]
    ) -> TripInput | None:
        """Return what a token of the call carries, `None` for unnamed guards."""


class ComponentFallback(Fallback):
    """The error UI of one component call, rendered from the call-site context.

    A deferred call falls back to the project default and mints no retry token itself.
    """

    __slots__ = ("_attempt", "_call", "_context", "_deferred", "_path")

    retry = staticmethod(retry_loads)

    def __init__(
        self,
        call: CallSite,
        context: Context,
        attempt: Attempt,
        path: Path,
        *,
        deferred: bool = False,
    ) -> None:
        """Hold the call site the error UI renders from."""
        self._call = call
        self._context = context
        self._attempt = attempt
        self._path = path
        self._deferred = deferred

    @override
    def retry_token(self, error: BoundaryFailure) -> str:
        """Return the retry token of a plain call declaring props whose rule passed.

        A deferred call gets its token from the sink that settles its wrapper instead.
        """
        attempt = self._attempt
        info, props = attempt.info, attempt.props
        if (
            self._deferred
            or not addressable(info)
            or props is None
            or not error.retry
            or error.kind in UNDISCLOSED
            or not (attempt.granted or not ruled(info))
        ):
            return ""
        context = self._context
        sink = deferral_sink(context)
        trip = self._call.token_input(info, DeferMode.INLINE, props)
        if sink is None or trip is None:
            return ""
        return sink.retry(context.get(REQUEST_KEY), trip)

    @override
    def tag(self) -> str | None:
        """Return the wrapper the call allows, `None` forcing an adopted root."""
        return self._call.wrapper(self._context, self._attempt.info)

    @override
    def render(self, error: BoundaryFailure, kind: ErrorKind) -> str:
        """Render `error.djx` from the call-site context, `error` and the props."""
        context = self._context
        render_ctx = self._call.file_context(context, self._path)
        render_ctx["error"] = error
        props = self._attempt.disclosed(kind)
        if props is None:
            render_ctx.pop("props", None)
        else:
            render_ctx["props"] = props
        return render_component_error(
            self._attempt.info,
            render_ctx,
            request=context.get(REQUEST_KEY),
            deferred=self._deferred,
        )


class DeferredRender:
    """How one deferred call renders its loading UI, its body and its error UI.

    `None` props resolve again inside the boundary, where their failure is caught.
    """

    __slots__ = (
        "_call",
        "_granted",
        "_guards",
        "_info",
        "_passed",
        "_path",
        "_props",
        "_tag",
        "_trip",
    )

    def __init__(
        self,
        call: CallSite,
        info: ComponentInfo,
        path: Path,
        props: dict[str, Any] | None,
        *,
        granted: bool,
        tag: str | None,
        trip: TripInput | None = None,
    ) -> None:
        """Hold the call with the props it resolved and whether its rule passed."""
        self._call = call
        self._info = info
        self._path = path
        self._props = props
        self._granted = granted
        self._tag = tag
        self._trip = trip
        self._passed = granted or not ruled(info)
        held = rendering_guards()
        self._guards: Guards = held if trip is None else (*held, *trip.perms)

    def trip_input(self) -> TripInput | None:
        """Return the input of a client-trip shell, `None` for another call."""
        trip = self._trip
        return trip if trip is not None and client_mode(trip.mode) else None

    def retry_input(self, failure: BoundaryFailure) -> TripInput | None:
        """Return the input a retry token is minted from, once the rule passed."""
        if not failure.retry or failure.kind in UNDISCLOSED or not self._passed:
            return None
        return self._trip

    def _rooted(self, html: str) -> tuple[str, BoundaryFailure | None]:
        """Return `html`, or the error a site adopting its root settles a misfit as.

        Such a site takes one root element, an empty body standing for a denied one.
        """
        if self._tag is not None or not html.strip():
            return html, None
        found = misrooted(html)
        if found is None:
            return html, None
        shown = new_digest()
        site = self._call.site
        logger.error(_MISROOTED_LOG, site, found, shown)
        mark_degraded()
        error = BoundaryFailure.of(
            ErrorKind.ERROR,
            shown,
            retry=retry_loads(),
            message=_MISROOTED % (site, found),
            traceback="",
        )
        return "", error

    def _attempt(self) -> Attempt:
        attempt = Attempt(self._info)
        attempt.props = self._props
        attempt.granted = self._granted
        return attempt

    def _contained[R](
        self,
        context: Context,
        attempt: Attempt,
        render: Callable[[], str],
        boundary: Callable[[Boundary, Callable[[], str], Fallback], R],
    ) -> R:
        """Run `render` inside the error boundary `boundary` draws, `contain` or bare.

        Its boundary claims the id of the wrapper around it, which a failure names.
        """
        fallback = ComponentFallback(
            self._call, context, attempt, self._path, deferred=True
        )
        guard = self._call.guard(context, self._info, None, claim_wrapper())
        token = current_attempt.set(attempt)
        try:
            with rendered_under(self._guards):
                return boundary(guard, render, fallback)
        finally:
            current_attempt.reset(token)

    def _component[R](
        self,
        context: Context,
        boundary: Callable[[Boundary, Callable[[], str], Fallback], R],
    ) -> R:
        attempt = Attempt(self._info)
        render = functools.partial(
            self._call.attempt, context, self._info, self._path, attempt, self._props
        )
        try:
            return self._contained(context, attempt, render, boundary)
        finally:
            self._passed = self._passed or attempt.granted

    def alone(self, context: Context) -> str:
        """Render the component in place behind its error boundary, without a sink."""
        return self._component(context, contain)

    def loading(self, context: Context) -> tuple[str, BoundaryFailure | None]:
        """Render the loading UI with the props a passed rule discloses.

        A deferred call inside it renders in place, as nothing assembles a skeleton.
        """
        attempt = self._attempt()

        def render() -> str:
            render_ctx = self._call.file_context(context, self._path)
            props = attempt.disclosed(ErrorKind.ERROR)
            if props is None:
                render_ctx.pop("props", None)
            else:
                render_ctx["props"] = props
            with loading_content():
                return render_component_loading(
                    self._info, render_ctx, request=context.get(REQUEST_KEY)
                )

        html, _error = self._contained(context, attempt, render, contain_bare)
        return self._rooted(html)

    def body(self, context: Context) -> tuple[str, BoundaryFailure | None]:
        """Render the component from the props of the call, the error UI unwrapped."""
        html, error = self._component(context, contain_bare)
        return self._rooted(html) if error is None else (html, error)

    def error(self, context: Context, kind: ErrorKind) -> tuple[str, BoundaryFailure]:
        """Render the error UI of `kind` without running the component."""
        fallback = ComponentFallback(
            self._call, context, self._attempt(), self._path, deferred=True
        )
        error = unrun_error(kind)
        return fallback.render(error, kind), error


def props_lookup(props: dict[str, Any]) -> Callable[[object], object]:
    """Return the object lookup of a rule checked on call-site props alone."""

    def lookup(name: object) -> object:
        return props.get(name) if isinstance(name, str) else None

    return lookup


def wrapper_source(info: ComponentInfo) -> str | None:
    """Return the file a `DEBUG` wrapper names, the loading UI before the error UI."""
    if not settings.DEBUG:
        return None
    files = info.files
    owner = None if files is None else files.loading or files.error
    owner = owner or info.module_path or info.template_path
    return None if owner is None else shown_source(owner)


__all__ = [
    "CallSite",
    "ComponentFallback",
    "DeferredRender",
    "props_lookup",
    "wrapper_source",
]
