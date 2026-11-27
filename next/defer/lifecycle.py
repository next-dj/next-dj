"""The part deferred boundaries take in the requests a page answers.

It claims a boundary reload by its token and answers it, gathers the deferred calls
of a render, and streams the page their render left jobs for.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final
from urllib.parse import unquote_plus

from django.http import HttpRequest, QueryDict

from next.lifecycle import Gate, ResponseSource, Share
from next.pages.access import refusal
from next.pages.guards import GUARD_RAISES
from next.partial.headers import partial_intent
from next.requests import RequestKind

from . import reload
from .assembly import PageRender, page_deferring, page_response
from .manager import defer_manager
from .markers import JOBLESS
from .payload import partial_streams
from .response import StreamingPageResponse
from .tokens.carrier import FIELD
from .trip import note_route, tripped


if TYPE_CHECKING:
    from contextvars import Token
    from types import TracebackType

    from django.http.request import _ImmutableQueryDict
    from django.http.response import HttpResponseBase

    from next.lifecycle import Claiming, PageCall, PageFacts


NAME: Final = "defer"
_CARRIED_ATTR: Final = "_next_boundary_token"


def _kept(fields: QueryDict, field: str = FIELD) -> _ImmutableQueryDict:
    """Return `fields` without the token `field`, as an immutable copy."""
    kept = QueryDict(mutable=True, encoding=fields.encoding)
    for name in fields:
        if name != field:
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
    if request.method != "POST":
        return
    carrier = defer_manager.tokens().carrier
    if "POST" in carrier.methods and carrier.name in request.POST:
        request.POST = _kept(request.POST, carrier.name)


def boundary_token(request: HttpRequest) -> str | None:
    """Return the token a partial request carries in the configured carrier.

    It is read once, before the query loses its token field.
    """
    held = getattr(request, _CARRIED_ATTR, None)
    if isinstance(held, str):
        return held or None
    token = None
    if partial_intent(request).partial:
        token = defer_manager.tokens().carrier.extract(request)
    setattr(request, _CARRIED_ATTR, token or "")
    _without_field(request)
    return token


def _claimed(request: HttpRequest) -> RequestKind | None:
    """Return `BOUNDARY` for a partial request with a token, dropping the token field.

    The partial switch forces a CORS preflight, so no cross-site page reaches a reload.
    """
    return None if boundary_token(request) is None else RequestKind.BOUNDARY


def _scrubbed(call: PageCall) -> None:
    """Drop the token field a page request carries, so its render never echoes it."""
    _without_field(call.request)


def _admitted(call: PageCall) -> HttpResponseBase | None:
    """Return the refusal of a boundary reload, the page access asked before its token.

    A method it does not answer and a request another site sent stop before the rest.
    """
    request = call.request
    permitted = reload.methods()
    if request.method not in permitted:
        return reload.not_allowed(permitted)
    if reload.cross_site(request):
        return reload.refused()
    try:
        denial = refusal(call.file_path, request, call.kwargs, call.dep_cache)
    except GUARD_RAISES as exc:
        return reload.failed(request, exc)
    return None if denial is None else reload.private(denial)


def _reloaded(call: PageCall) -> HttpResponseBase:
    """Answer the reload of one boundary the token of the request names."""
    request = call.request
    held = getattr(request, _CARRIED_ATTR, "")
    token = held if isinstance(held, str) else ""
    return reload.answer(request, call.file_path, call.kwargs, token, call.dep_cache)


class _Noted:
    """The block that keeps the URL kwargs a trip token of the request binds."""

    __slots__ = ("_call",)

    def __init__(self, call: PageCall) -> None:
        """Hold the call whose kwargs are kept."""
        self._call = call

    def __enter__(self) -> None:
        """Keep the kwargs on the request."""
        note_route(self._call.request, self._call.kwargs)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Keep them, as the response may still mint a token."""


class _Gathered:
    """What a page render gathered to defer, as the view reads it after."""

    __slots__ = ("_request", "rendering")

    def __init__(self, rendering: PageRender, request: HttpRequest) -> None:
        """Hold the render and the request it answers."""
        self.rendering = rendering
        self._request = request

    def release(self) -> None:
        """Give back the stream slot no stream response took."""
        self.rendering.release()

    def complete(self) -> bool:
        """Whether the render left no job and no client-trip wrapper behind.

        A delivery that answers the shell skipped its jobs, and a client trip waits.
        """
        if tripped(self._request):
            return False
        delivered = self.rendering.delivered
        return delivered is None or delivered.delivery not in JOBLESS


class _Gathering:
    """The block that gathers the deferred calls of a page render."""

    __slots__ = ("_call", "_inner")

    def __init__(self, call: PageCall) -> None:
        """Hold the call the gathered render is kept on."""
        self._call = call
        self._inner = page_deferring(call.request)

    def __enter__(self) -> None:
        """Open the gathering and keep what it gathers on the call."""
        rendering = self._inner.__enter__()
        self._call.deferral = _Gathered(rendering, self._call.request)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Close the gathering, a failed render giving its stream slot back."""
        return self._inner.__exit__(exc_type, exc, traceback)


class _Streams:
    """The block that tells the runtime partials may stream, as the policy says."""

    __slots__ = ("_request", "_token")

    def __init__(self, call: PageCall) -> None:
        """Hold the request the policy reads."""
        self._request = call.request

    def __enter__(self) -> None:
        """Set the flag the init payload carries."""
        streams = defer_manager.policy().streams_partials(self._request)
        self._token: Token[bool] = partial_streams.set(streams)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Clear it, whatever the render did."""
        partial_streams.reset(self._token)


def _streamed(call: PageCall, html: str) -> HttpResponseBase:
    """Return the response of a rendered page, its stream when its render left one."""
    deferral = call.deferral
    rendering = deferral.rendering if isinstance(deferral, _Gathered) else None
    return page_response(html, rendering, call.request, call.file_path)


def _settled(call: PageCall, response: HttpResponseBase) -> HttpResponseBase:
    """Settle the headers of a page stream once every other step set its own."""
    if call.source is ResponseSource.RENDER and isinstance(
        response, StreamingPageResponse
    ):
        response.settle()
    return response


_GATES: Final = {
    RequestKind.BOUNDARY: Gate(admit=_admitted, answers=True),
    RequestKind.PAGE: Gate(admit=_scrubbed),
}
_RELOAD: Final = Share(around=_Noted, serve=_reloaded)


class DeferParticipant:
    """The deferred boundaries' part in the requests a page answers."""

    name = NAME

    def claim(self) -> Claiming:
        """Return the claim of a boundary reload."""
        return _claimed

    def gate(self, kind: RequestKind) -> Gate | None:
        """Return the reload gate, which answers its access, and the page scrub."""
        return _GATES.get(kind)

    def share(self, facts: PageFacts) -> Share | None:
        """Return the reload, or the gathering, the stream and the noted route."""
        kind = facts.kind
        if kind is RequestKind.BOUNDARY:
            return _RELOAD
        noted = _Noted if facts.defers else None
        if kind is RequestKind.ZONE:
            return None if noted is None else Share(around=noted)
        if facts.defers:
            if kind is RequestKind.NAVIGATE:
                return Share(around=noted, block=_Gathering)
            return Share(
                around=noted, block=_Gathering, respond=_streamed, finish=_settled
            )
        return Share(block=_Streams) if facts.streams else None


__all__ = ["NAME", "DeferParticipant", "boundary_token"]
