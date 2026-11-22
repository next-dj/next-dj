"""How a boundary guards a render and settles its failure into the error UI."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from django.conf import settings
from django.core.exceptions import SuspiciousOperation
from django.http.multipartparser import MultiPartParserError

from next.defer import BoundaryFailure
from next.diagnostics import mark_degraded
from next.transactions import probe_atomic_aliases

from .failures import Boundary, Fallback, Trace, contained, kind_of, shown_kind
from .markup import Shown, error_attributes, note_retry, wrap
from .protection import Protection, guarded
from .reporting import report
from .scope import Render, Scope, current
from .signals import boundary_failed


if TYPE_CHECKING:
    from collections.abc import Callable, Hashable

    from .options import Errors


_SECURITY_LOGGED: Final = (SuspiciousOperation, MultiPartParserError)


@dataclass(frozen=True, slots=True)
class _Failure:
    """The error UI one contained failure rendered, before its wrapper."""

    body: str
    error: BoundaryFailure
    attributes: str
    shown: Shown


def _outcome(
    boundary: Boundary, render: Callable[[], str], fallback: Fallback
) -> str | _Failure:
    """Return what `render` returns, or the failure the boundary contained."""
    state = current()
    if state is not None:
        return _guard(state, boundary, render, fallback)
    scope = Scope()
    with scope:
        return _guard(scope.state, boundary, render, fallback)


def contain(boundary: Boundary, render: Callable[[], str], fallback: Fallback) -> str:
    """Return what `render` returns, or the error UI of `fallback` when it fails.

    Outside `render_boundaries` the boundary opens the per-render state for itself.
    """
    outcome = _outcome(boundary, render, fallback)
    if isinstance(outcome, str):
        return outcome
    return wrap(fallback.tag(), outcome.attributes, outcome.body, outcome.shown)


def contain_bare(
    boundary: Boundary, render: Callable[[], str], fallback: Fallback
) -> tuple[str, BoundaryFailure | None]:
    """Return what `contain` returns without the wrapper, with the error it settled as.

    A deferred boundary draws the wrapper itself, around a job's settled content.
    """
    outcome = _outcome(boundary, render, fallback)
    if isinstance(outcome, str):
        return outcome, None
    return outcome.body, outcome.error


def _owner(boundary: Boundary) -> Hashable:
    owner = boundary.owner
    return (boundary.addressing, boundary.name) if owner is None else owner


def _guard(
    state: Render, boundary: Boundary, render: Callable[[], str], fallback: Fallback
) -> str | _Failure:
    failing = state.failing
    if failing and _owner(boundary) in failing:
        return render()
    errors = state.settings()
    atomic = errors.atomic
    aliases = probe_atomic_aliases() if atomic is False else state.aliases
    protection = Protection.open(aliases, atomic, boundary.collector)
    return guarded(protection, render, _held, state, boundary, fallback, errors)


def _held(
    exc: Exception,
    state: Render,
    boundary: Boundary,
    fallback: Fallback,
    errors: Errors,
) -> _Failure | None:
    """Return the failure the boundary contains, `None` for one it lets propagate."""
    if errors.reraise or not contained(exc, started=boundary.started):
        return None
    return _settle(state, boundary, exc, fallback, errors)


def _settle(
    state: Render,
    boundary: Boundary,
    exc: Exception,
    fallback: Fallback,
    errors: Errors,
) -> _Failure:
    """Report a contained failure, then render the error UI outside the guard."""
    mark_degraded()
    kind = kind_of(exc)
    if boundary.started and isinstance(exc, _SECURITY_LOGGED):
        logging.getLogger(f"django.security.{type(exc).__name__}").error(
            str(exc),
            exc_info=exc,
            extra={"status_code": 400, "request": boundary.request},
        )
    digest = report(boundary, exc, kind, errors)
    ident = state.next_id() if boundary.ident is None else boundary.ident
    if boundary_failed.has_listeners(contain):
        boundary_failed.send_robust(
            sender=contain,
            id=ident,
            name=boundary.name,
            addressing=boundary.addressing,
            kind=kind,
            digest=digest,
            exception=exc,
            request=boundary.request,
        )
    shown = shown_kind(kind, opaque=errors.opaque)
    debug = bool(settings.DEBUG)
    trace = Trace.of(exc) if debug else None
    error = BoundaryFailure.of(
        shown,
        digest,
        retry=fallback.retry(),
        message="" if trace is None else trace.line,
        traceback="" if trace is None else trace.text,
    )
    owner = _owner(boundary)
    state.failing.add(owner)
    try:
        body = fallback.render(error, kind)
    finally:
        state.failing.discard(owner)
    site = boundary.site or f"the {boundary.addressing} {boundary.name!r}"
    attributes = error_attributes(
        ident, shown, digest if debug else None, boundary.source if debug else None
    )
    token = fallback.retry_token(error)
    note_retry(boundary.collector, body, token)
    return _Failure(
        body,
        error,
        attributes + token,
        Shown(trace if errors.detail else None, site, debug=debug),
    )


__all__ = ["contain", "contain_bare"]
