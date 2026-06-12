"""HTTP request parsing utilities for form dispatch."""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from django.urls import Resolver404, get_script_prefix, resolve

from next.deps import RESERVED_KEYS

from .uid import URL_NAME_FORM_ACTION, validated_origin_path


if TYPE_CHECKING:
    from django.http import HttpRequest


_ORIGIN_MATCH_ATTR = "_next_form_origin_match"
_UNSET = object()


@dataclass(frozen=True, slots=True)
class _OriginMatch:
    """Resolved identity of the page named by the posted origin field."""

    page_path: "Path | None"
    url_kwargs: dict[str, object]
    origin: str


def _filter_reserved_url_kwargs(url_kwargs: dict[str, object]) -> dict[str, object]:
    """Drop keys that collide with DI names used by `resolve_dependencies`."""
    return {k: v for k, v in url_kwargs.items() if k not in RESERVED_KEYS}


def _page_path_from_view(view: object) -> "Path | None":
    """Return the `next_page_path` attribute of a resolved view, if any."""
    raw = getattr(view, "next_page_path", None)
    if isinstance(raw, Path):
        return raw
    if isinstance(raw, str):
        return Path(raw)
    return None


def _resolve_origin_match(request: "HttpRequest") -> "_OriginMatch | None":
    """Resolve the posted `_next_form_origin` against the URLconf."""
    raw = request.POST.get("_next_form_origin") if hasattr(request, "POST") else None
    origin = validated_origin_path(raw)
    if origin is None:
        return None
    path = origin.partition("?")[0]
    prefix = get_script_prefix()
    if prefix != "/" and path.startswith(prefix):
        path = "/" + path.removeprefix(prefix)
    try:
        match = resolve(path, urlconf=getattr(request, "urlconf", None))
    except Resolver404:
        return None
    return _OriginMatch(
        page_path=_page_path_from_view(match.func),
        url_kwargs=_filter_reserved_url_kwargs(dict(match.kwargs)),
        origin=origin,
    )


def _resolve_origin(request: "HttpRequest") -> "_OriginMatch | None":
    """Return the origin match for the request, memoised on the request."""
    cached = getattr(request, _ORIGIN_MATCH_ATTR, _UNSET)
    if cached is not _UNSET:
        return cast("_OriginMatch | None", cached)
    match = _resolve_origin_match(request)
    setattr(request, _ORIGIN_MATCH_ATTR, match)
    return match


def _url_kwargs_for_request(request: "HttpRequest") -> dict[str, object]:
    """Return the URL kwargs of the page the request renders or re-renders."""
    match = getattr(request, "resolver_match", None)
    if match is not None and getattr(match, "url_name", None) == URL_NAME_FORM_ACTION:
        origin_match = _resolve_origin(request)
        return dict(origin_match.url_kwargs) if origin_match is not None else {}
    if match is not None and getattr(match, "kwargs", None):
        return _filter_reserved_url_kwargs(dict(match.kwargs))
    if getattr(request, "method", None) == "POST":
        origin_match = _resolve_origin(request)
        if origin_match is not None:
            return dict(origin_match.url_kwargs)
    return {}


__all__ = [
    "_OriginMatch",
    "_filter_reserved_url_kwargs",
    "_resolve_origin",
    "_url_kwargs_for_request",
]
