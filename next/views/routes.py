"""The kinds of response a page URL answers with and the per-request decision."""

import enum
from typing import TYPE_CHECKING, Final
from urllib.parse import unquote_plus

from django.http import HttpRequest, QueryDict

from next.conf.router import router_enabled
from next.pages.responses import Depth, Purpose, mark_purpose
from next.partial.headers import PartialIntent, partial_intent
from next.suspense.manager import suspense_manager
from next.suspense.tokens.transport import FIELD, BodyTransport


if TYPE_CHECKING:
    from django.http.request import _ImmutableQueryDict


_CARRIED_ATTR: Final = "_next_boundary_token"
_SPECULATIVE: Final = "prefetch"


class Route(enum.StrEnum):
    """The kind of response a request to a page URL is answered with."""

    PAGE = "page"
    ZONE = "zone"
    BOUNDARY = "boundary"
    NAVIGATE = "navigate"


def _kept(fields: QueryDict) -> "_ImmutableQueryDict":
    """Return `fields` without the token field, as an immutable copy."""
    kept = QueryDict(mutable=True, encoding=fields.encoding)
    for name in fields:
        if name != FIELD:
            kept.setlist(name, fields.getlist(name))
    return QueryDict(kept.urlencode(), encoding=fields.encoding)


def _raw_kept(raw: str) -> str:
    """Return a raw query string without the token field, every other pair as sent."""
    pairs = raw.split("&")
    return "&".join(
        pair for pair in pairs if unquote_plus(pair.partition("=")[0]) != FIELD
    )


def _without_field(request: HttpRequest) -> None:
    """Drop the token field from the query string and a body form, so nothing echoes it.

    The raw ASGI `scope` alone keeps the field, and a query-less GET stays unparsed.
    """
    if not (
        request.META.get("QUERY_STRING")
        or request.method == "POST"
        or "GET" in request.__dict__
    ):
        return
    query = request.GET
    if FIELD in query:
        request.GET = _kept(query)
        meta = request.META
        meta["QUERY_STRING"] = _raw_kept(str(meta.get("QUERY_STRING", "")))
    if request.method == "POST" and isinstance(
        suspense_manager.tokens().transport, BodyTransport
    ):
        form = request.POST
        if FIELD in form:
            request.POST = _kept(form)


def boundary_token(request: HttpRequest) -> str | None:
    """Return the token a partial request carries through the configured transport.

    It is read once, before the query loses its token field.
    """
    held = getattr(request, _CARRIED_ATTR, None)
    if isinstance(held, str):
        return held or None
    token = None
    if partial_intent(request).partial:
        token = suspense_manager.tokens().transport.extract(request)
    setattr(request, _CARRIED_ATTR, token or "")
    _without_field(request)
    return token


def _decided(request: HttpRequest, intent: PartialIntent) -> Route:
    """Return the route of a request whose intent is `intent`."""
    if boundary_token(request) is not None:
        return Route.BOUNDARY
    if intent.zones:
        return Route.ZONE
    if intent.navigate and router_enabled():
        return Route.NAVIGATE
    return Route.PAGE


def decide_route(request: HttpRequest) -> Route:
    """Return `BOUNDARY` for a token with `X-Next-Request: 1`, `ZONE` for zones.

    A navigation is `NAVIGATE` only while the client router is on, else a plain page.
    The partial switch forces a CORS preflight, so no cross-site page reaches a reload.
    """
    return _decided(request, partial_intent(request))


def note_purpose(request: HttpRequest, route: Route) -> None:
    """Set `request.next` to why the view renders, a browser prefetch a speculation."""
    if route is Route.NAVIGATE:
        intent = partial_intent(request)
        if intent.prefetch:
            depth = Depth.FULL if intent.full else Depth.SHELL
            mark_purpose(request, Purpose.PREFETCH, depth)
        else:
            mark_purpose(request, Purpose.NAVIGATION)
    elif _SPECULATIVE in request.META.get("HTTP_SEC_PURPOSE", ""):
        mark_purpose(request, Purpose.SPECULATION)
    else:
        mark_purpose(request, Purpose.DOCUMENT)


def route_request(request: HttpRequest, intent: PartialIntent) -> Route:
    """Return the route of `request` with its purpose noted, both from `intent`.

    Without the partial switch it is a page, and no token is read for it.
    """
    if intent.partial:
        route = _decided(request, intent)
    else:
        route = Route.PAGE
        _without_field(request)
    note_purpose(request, route)
    return route


__all__ = ["Route", "boundary_token", "decide_route", "note_purpose", "route_request"]
