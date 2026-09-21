"""The request a page's own view would have seen for a GET visit of one of its URLs."""

import copy
from typing import TYPE_CHECKING

from django.http import QueryDict
from django.urls import get_script_prefix


if TYPE_CHECKING:
    from django.http import HttpRequest


def visit_request(request: "HttpRequest", url: str | None) -> "HttpRequest":
    """Return a copy of `request` that presents a GET visit of `url`.

    A page answers an out-of-band caller through the same `render()` its own view runs,
    so that `render()` has to read the page, not the endpoint that asked on its behalf.
    The copy keeps whatever a middleware attached, the user and the session included.
    """
    path, _, query = (url or "").partition("?")
    visit = copy.copy(request)
    meta = {**request.META, "REQUEST_METHOD": "GET", "QUERY_STRING": query}
    visit.method = "GET"
    visit.GET = QueryDict(query)
    visit.POST = QueryDict()
    visit.resolver_match = None
    if path:
        info = _path_info(path)
        meta["PATH_INFO"] = info
        visit.path = path
        visit.path_info = info
    visit.META = meta
    return visit


def _path_info(path: str) -> str:
    """Return `path` without the script prefix a sub-path deployment mounts under."""
    prefix = get_script_prefix()
    if prefix != "/" and path.startswith(prefix):
        return "/" + path.removeprefix(prefix)
    return path


__all__ = ["visit_request"]
