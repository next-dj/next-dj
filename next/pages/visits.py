"""The request a page's own view would have seen for a GET visit of one of its URLs."""

import copy
from typing import TYPE_CHECKING

from django.http import QueryDict
from django.urls import get_script_prefix

from next.deps import REQUEST_DEP_CACHE_ATTR
from next.utils import decode_url_path


if TYPE_CHECKING:
    from django.http import HttpRequest


def visit_request(request: "HttpRequest", url: str | None) -> "HttpRequest":
    """Return a copy of `request` that presents a GET visit of `url`.

    An out-of-band caller runs the same `render()` as the page view, so that `render()`
    must see the page URL rather than the endpoint that called it. The copy keeps what
    a middleware attached, but not the dependency cache of a form dispatch.
    """
    path, _, query = (url or "").partition("?")
    visit = copy.copy(request)
    meta = {**request.META, "REQUEST_METHOD": "GET", "QUERY_STRING": query}
    visit.method = "GET"
    visit.GET = QueryDict(query)
    visit.POST = QueryDict()
    visit.resolver_match = None
    vars(visit).pop(REQUEST_DEP_CACHE_ATTR, None)
    if path:
        path = decode_url_path(path)
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
