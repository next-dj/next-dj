"""The kind of one page request and the purpose its view renders for."""

from typing import Final

from django.http import HttpRequest

from next.lifecycle import lifecycle
from next.partial.headers import partial_intent
from next.requests import Purpose, RequestKind, mark_purpose


_SPECULATIVE: Final = "prefetch"


def note_purpose(request: HttpRequest, route: RequestKind) -> None:
    """Set `request.next` to why the view renders, a browser prefetch a speculation."""
    if route is RequestKind.NAVIGATE:
        depth = partial_intent(request).prefetch
        if depth is not None:
            mark_purpose(request, Purpose.PREFETCH, depth)
        else:
            mark_purpose(request, Purpose.NAVIGATE)
    elif _SPECULATIVE in request.META.get("HTTP_SEC_PURPOSE", ""):
        mark_purpose(request, Purpose.SPECULATION)
    else:
        mark_purpose(request, Purpose.DOCUMENT)


def route_request(request: HttpRequest) -> RequestKind:
    """Return the kind of `request` the participants claim, its purpose noted.

    Without the partial switch, or with no claim, it is a page.
    """
    partial = partial_intent(request).partial
    route = lifecycle.kind(request) if partial else RequestKind.PAGE
    note_purpose(request, route)
    return route


__all__ = ["note_purpose", "route_request"]
