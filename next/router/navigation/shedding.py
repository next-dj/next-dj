"""The answers a navigation meets before any render, refused, stale or shed."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

from django.http import HttpResponse

from next.pages.responses import never_store
from next.partial.headers import partial_intent, set_partial_vary
from next.partial.manager import asset_version
from next.partial.stream import PROTOCOL
from next.router.manager import client_router
from next.router.signals import prefetch_shed

from .state import Routing


if TYPE_CHECKING:
    from pathlib import Path

    from django.http import HttpRequest


_PREFETCH_REFUSED: Final = 204
_SHED: Final = 429
_CONFLICT: Final = 409
_PROTOCOL: Final = str(PROTOCOL)


def _private(response: HttpResponse) -> HttpResponse:
    """Return `response`, never stored and varied on the partial request headers."""
    never_store(response)
    set_partial_vary(response)
    return response


def refused_prefetch() -> HttpResponse:
    """Return the empty answer to a prefetch of a page that refuses prefetches."""
    return _private(HttpResponse(b"", status=_PREFETCH_REFUSED))


def stale(request: HttpRequest) -> HttpResponse | None:
    """Return the 409 a navigation asserting another asset version or protocol meets."""
    intent = partial_intent(request)
    other = intent.protocol is not None and intent.protocol != _PROTOCOL
    if not other and (not intent.version or intent.version == asset_version()):
        return None
    return _private(HttpResponse(b"", status=_CONFLICT))


def shed(request: HttpRequest, page_path: Path) -> HttpResponse | None:
    """Return the 429 a prefetch answers once `ROUTER["SHED"]` sheds it, else `None`."""
    waited = client_router.shed().waited(request, page_path)
    if waited is None:
        return None
    response = _private(HttpResponse(b"", status=_SHED))
    response["Retry-After"] = str(max(1, math.ceil(waited)))
    if prefetch_shed.has_listeners(Routing):
        prefetch_shed.send_robust(
            sender=Routing,
            page_path=page_path,
            waited_ms=waited * 1000,
            request=request,
        )
    return response


__all__ = ["refused_prefetch", "shed", "stale"]
