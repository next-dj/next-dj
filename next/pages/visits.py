"""The request a page's own view would have seen for a GET visit of one of its URLs."""

import copy
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from django.http import QueryDict
from django.urls import get_script_prefix

from next.csrf import TOKEN_META
from next.requests import NEXT_ATTR, next_of
from next.utils import decode_url_path


if TYPE_CHECKING:
    from django.http import HttpRequest


def visit_request(request: "HttpRequest", url: str | None) -> "HttpRequest":
    """Return a copy of `request` that presents a GET visit of `url`.

    An out-of-band caller's `render()` must see the page URL, not the endpoint's.
    """
    path, _, query = (url or "").partition("?")
    visit = copy.copy(request)
    vars(visit)[NEXT_ATTR] = next_of(request).fork()
    meta = {**request.META, "REQUEST_METHOD": "GET", "QUERY_STRING": query}
    visit.method = "GET"
    visit.GET = QueryDict(query)
    visit.POST = QueryDict()
    visit.resolver_match = None
    if path:
        path = decode_url_path(path)
        info = _path_info(path)
        meta["PATH_INFO"] = info
        visit.path = path
        visit.path_info = info
    visit.META = meta
    return visit


def hand_back(visit: "HttpRequest", request: "HttpRequest") -> None:
    """Copy onto `request` what a render of `visit` marked for the response it sends.

    A token the render read sets the CSRF cookie of that response through `META`.
    """
    next_of(request).adopt(next_of(visit))
    meta = visit.META
    for key in TOKEN_META:
        if key in meta:
            request.META[key] = meta[key]


@contextmanager
def visiting(request: "HttpRequest", url: str | None) -> Iterator["HttpRequest"]:
    """Yield the visit of `url` as `visit_request` builds it, handed back on exit."""
    visit = visit_request(request, url)
    try:
        yield visit
    finally:
        hand_back(visit, request)


def _path_info(path: str) -> str:
    """Return `path` without the script prefix a sub-path deployment mounts under."""
    prefix = get_script_prefix()
    if prefix != "/" and path.startswith(prefix):
        return "/" + path.removeprefix(prefix)
    return path


__all__ = ["hand_back", "visit_request", "visiting"]
