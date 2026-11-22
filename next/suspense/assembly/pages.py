"""The response of a rendered page, its stream when the render left one."""

from pathlib import Path
from typing import Final

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from next.conf.defer import defer_option
from next.static import get_static_manager
from next.static.inject import split_of
from next.static.nonce import CSP_MIDDLEWARE, request_nonce, resolve_nonce
from next.suspense.policy import Delivered
from next.suspense.render import Delta
from next.suspense.response import Chunks, StreamingPageResponse, dress
from next.suspense.session import BoundarySession
from next.utils import middleware_installed

from .delivery import PageRender
from .jobs import Source, prepare


_STREAM_HEADER: Final = "X-Next-Stream"
_BOUNDARIES_HEADER: Final = "X-Next-Boundaries"


def _listed_header(boundaries: tuple[tuple[str, str], ...]) -> str:
    return ",".join(f"{name}={mode}" for name, mode in boundaries)


def _stream(
    html: str,
    rendering: PageRender,
    request: HttpRequest,
    *,
    session: BoundarySession[Delta],
    delivered: Delivered,
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
        Source(session, request),
        chunks,
        grant=rendering.grant(delivered, rendering.boundaries, page_path),
    )
    rendering.disown()
    if settings.DEBUG:
        response[_STREAM_HEADER] = delivered.delivery.value
        response[_BOUNDARIES_HEADER] = _listed_header(rendering.boundaries)
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
            response[_STREAM_HEADER] = f"{delivered.delivery} ({delivered.reason})"
            response[_BOUNDARIES_HEADER] = _listed_header(rendering.boundaries)
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
