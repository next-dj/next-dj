"""Mint the token a client-trip boundary or the retry of an error wrapper carries."""

import logging
from collections.abc import Mapping
from typing import Final

from django.http import HttpRequest
from django.utils.html import escape

from next.boundaries.calls import DeferredCall
from next.boundaries.failures import BoundaryFailure
from next.components.trip import (
    TripInput,
    TripSource,
    client_mode,
    component_scope,
    prop_types,
    token_overrides,
)
from next.diagnostics import FailureLog
from next.pages.responses import mark_bound_render
from next.partial.keys import CARRIER_ATTR, TRIP_ATTR

from .errors import (
    BoundaryBindingError,
    BoundaryMintError,
    BoundarySizeError,
    BoundaryStorageError,
    BoundaryValueError,
)
from .manager import defer_manager
from .state import state_of
from .tokens import BoundaryClaims, encode_props
from .tokens.carrier import CARRIERS


logger = logging.getLogger("next.defer")
_failures = FailureLog(logger)

_DEFAULT_PLACE: Final = CARRIERS["header"].describe()
_UNBOUND: Final = (
    "Component %r renders as a stream boundary in this response, as its token binding "
    "refused the visitor: %s"
)
_OVERSIZED: Final = (
    "Component %r renders as a stream boundary in this response, as its props outgrow "
    "the token: %s"
)
_NON_FINITE: Final = (
    "Component %r renders as a stream boundary in this response, as its props hold a "
    "number no token carries: %s"
)
_UNSTORED: Final = (
    "Component %r renders as a stream boundary in this response, as its token storage "
    "failed: %s"
)
_NO_RETRY: Final = "The error wrapper of %r offers no retry token: %s"


def note_route(request: HttpRequest, kwargs: Mapping[str, object]) -> None:
    """Keep the URL kwargs of the page `request` reached, which a token is bound to."""
    state_of(request).route = dict(kwargs)


def route_kwargs(request: HttpRequest) -> Mapping[str, object]:
    """Return the URL kwargs of the page `request` reached, its resolver match else."""
    held = state_of(request).route
    if held is not None:
        return held
    match = getattr(request, "resolver_match", None)
    return {} if match is None else match.kwargs


def tripped(request: HttpRequest) -> bool:
    """Whether the render of `request` left a client-trip wrapper with a token."""
    return state_of(request).tripped


def claims_for(request: HttpRequest, trip: TripInput, limit: int) -> BoundaryClaims:
    """Return the claims of one call, its props typed by what its context reads.

    A prop that is unencodable or holds text over `limit` raises `BoundaryPropsError`.
    """
    info = trip.info
    return BoundaryClaims.of(
        info.name,
        component_scope(info),
        props=encode_props(trip.props, prop_types(info), limit=limit),
        url_path=request.path,
        url_kwargs=route_kwargs(request),
        perms=trip.perms,
    )


def mint(request: HttpRequest, trip: TripInput) -> str:
    """Mint the token of one call, a bound one making the response private."""
    backend = defer_manager.tokens()
    overrides = token_overrides(trip.info)
    claims = claims_for(request, trip, backend.prop_limit(overrides))
    token = backend.issue(request, claims, tokens=overrides)
    if backend.bound(request, overrides):
        mark_bound_render(request)
    return token


def token_attributes(token: str) -> str:
    """Return the wrapper attributes of a token and of a non-default place."""
    text = f' {TRIP_ATTR}="{escape(token)}"'
    place = defer_manager.tokens().carrier.describe()
    if place != _DEFAULT_PLACE:
        text += f' {CARRIER_ATTR}="{escape(place.attribute)}"'
    return text


def trip_input(call: DeferredCall) -> TripInput | None:
    """Return the client-trip input of `call`, `None` for a call the job delivers."""
    renderer = call.renderer
    if not client_mode(call.site.mode) or not isinstance(renderer, TripSource):
        return None
    return renderer.trip_input()


def shell_token(request: object, trip: TripInput) -> str | None:
    """Return the token attributes of a client-trip wrapper, `None` to stream instead.

    A refused binding, a prop the token cannot hold or a failed storage streams it.
    """
    if not isinstance(request, HttpRequest):
        return None
    try:
        token = mint(request, trip)
    except BoundaryBindingError as exc:
        _failures.emit(
            logging.DEBUG, ("unbound", trip.info.name), _UNBOUND, (trip.info.name, exc)
        )
        return None
    except BoundarySizeError as exc:
        _failures.emit(
            logging.WARNING,
            ("oversized", trip.info.name),
            _OVERSIZED,
            (trip.info.name, exc),
        )
        return None
    except BoundaryValueError as exc:
        _failures.emit(
            logging.WARNING,
            ("non_finite", trip.info.name),
            _NON_FINITE,
            (trip.info.name, exc),
        )
        return None
    except BoundaryStorageError as exc:
        _failures.emit(
            logging.ERROR,
            ("unstored", trip.info.name),
            _UNSTORED,
            (trip.info.name, exc),
            exc_info=exc,
        )
        return None
    state_of(request).tripped = True
    return token_attributes(token)


def retry_attributes(request: object, trip: TripInput) -> str:
    """Return the token attributes a failed wrapper of `trip` retries with, or nothing.

    A prop, a binding or a storage that cannot mint drops the retry.
    """
    if not isinstance(request, HttpRequest):
        return ""
    try:
        token = mint(request, trip)
    except BoundaryMintError as exc:
        _failures.emit(
            logging.WARNING, ("retry", trip.info.name), _NO_RETRY, (trip.info.name, exc)
        )
        return ""
    return token_attributes(token)


def retry_token(request: object, call: DeferredCall, failure: BoundaryFailure) -> str:
    """Return the token attributes the error wrapper of `call` offers a retry with.

    The rule must have passed, as the renderer names no input otherwise.
    """
    renderer = call.renderer
    if not isinstance(renderer, TripSource):
        return ""
    trip = renderer.retry_input(failure)
    return "" if trip is None else retry_attributes(request, trip)


__all__ = [
    "claims_for",
    "mint",
    "note_route",
    "retry_attributes",
    "retry_token",
    "route_kwargs",
    "shell_token",
    "token_attributes",
    "trip_input",
    "tripped",
]
