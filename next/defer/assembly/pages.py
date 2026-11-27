"""The response of a rendered page, its stream when the render left one."""

from pathlib import Path

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from next.conf.defer import defer_option
from next.defer.ledger import ResponseJobs
from next.defer.policy import DeliveryDecision
from next.defer.response import Chunks, StreamingPageResponse, dress
from next.partial.headers import BOUNDARIES, STREAM
from next.partial.keys import NOSCRIPT_PARAM
from next.static import get_static_manager
from next.static.inject import split_of
from next.static.nonce import CSP_MIDDLEWARE, request_nonce, resolve_nonce
from next.utils import middleware_installed

from .delivery import PageRender
from .jobs import StreamSource, grant, prepare


def _listed_header(boundaries: tuple[tuple[str, str], ...]) -> str:
    return ",".join(f"{name}={mode}" for name, mode in boundaries)


def _assembled(request: HttpRequest) -> str | None:
    """Return the query of the page assembled whole, `None` for a request already so.

    A policy that streams the flagged request anyway must not send its reader around.
    """
    if request.GET.get(NOSCRIPT_PARAM) == "1":
        return None
    query = request.GET.copy()
    query[NOSCRIPT_PARAM] = "1"
    return f"?{query.urlencode()}"


def _stream(
    html: str,
    rendering: PageRender,
    request: HttpRequest,
    *,
    session: ResponseJobs,
    delivered: DeliveryDecision,
    page_path: Path | None,
) -> StreamingPageResponse:
    """Settle the request and the headers, then return the stream of the shell.

    Every chunk, the end marker and the hiding style carry the nonce the shell used.
    """
    prepare(session, request, stream=True)
    if any(map(middleware_installed, CSP_MIDDLEWARE)):
        request_nonce(request)
    nonce = resolve_nonce(request)
    collector = rendering.collector
    split = None if collector is None else split_of(collector)
    document = dress(
        html,
        len(html) if split is None else split,
        nonce=nonce,
        noscript=defer_option("NOSCRIPT") is not False,
        refresh=_assembled(request),
    )
    chunks = Chunks(
        request,
        shell=rendering.shell,
        collector=collector,
        builder=get_static_manager().script_builder(),
        nonce=nonce,
    )
    response = StreamingPageResponse(
        request,
        document,
        StreamSource(session, request),
        chunks,
        grant=grant(rendering, delivered, rendering.boundaries, page_path),
    )
    rendering.slot = None
    if settings.DEBUG:
        response[STREAM] = delivered.delivery.value
        response[BOUNDARIES] = _listed_header(rendering.boundaries)
    return response


def page_response(
    html: str,
    rendering: PageRender | None,
    request: HttpRequest,
    page_path: Path | None = None,
) -> HttpResponse | StreamingPageResponse:
    """Return the response of a rendered page, its stream when the render left one.

    Under `DEBUG` an assembled page names why it did not stream and its boundaries.
    """
    if rendering is None:
        return HttpResponse(html)
    session = rendering.session
    delivered = rendering.delivered
    if session is None or delivered is None:
        rendering.release()
        response = HttpResponse(html)
        if settings.DEBUG and delivered is not None and rendering.boundaries:
            response[STREAM] = f"{delivered.delivery} ({delivered.reason})"
            response[BOUNDARIES] = _listed_header(rendering.boundaries)
        return response
    try:
        return _stream(
            html,
            rendering,
            request,
            session=session,
            delivered=delivered,
            page_path=page_path,
        )
    finally:
        rendering.release()


__all__ = ["page_response"]
