"""The `X-Robots-Tag` a site closed to search stamps on every response it builds."""

import functools
from collections.abc import Callable
from typing import Concatenate, Final

from django.http import HttpRequest
from django.http.response import HttpResponseBase

from .config import site_indexable


ROBOTS_HEADER: Final = "X-Robots-Tag"
"""The response header that carries robots directives outside the HTML head."""

CLOSED_ROBOTS: Final = "noindex, nofollow"
"""The robots directives every page and file of a site closed to search carries."""


def stamp_site_robots[R: HttpResponseBase](response: R, request: HttpRequest) -> R:
    """Overwrite the robots header on a site closed to search, then return `response`.

    An open site leaves the header to the page, so a noindex it declares survives.
    """
    if not site_indexable(request):
        response[ROBOTS_HEADER] = CLOSED_ROBOTS
    return response


def site_robots[**P, R: HttpResponseBase](
    view: Callable[Concatenate[HttpRequest, P], R],
) -> Callable[Concatenate[HttpRequest, P], R]:
    """Wrap a view so what it answers on a site closed to search carries noindex."""

    @functools.wraps(view)
    def wrapped(request: HttpRequest, /, *args: P.args, **kwargs: P.kwargs) -> R:
        return stamp_site_robots(view(request, *args, **kwargs), request)

    return wrapped


__all__ = ["CLOSED_ROBOTS", "ROBOTS_HEADER", "site_robots", "stamp_site_robots"]
