"""Server-side resolution of the posted form origin to the page it names."""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from django.urls import Resolver404, get_script_prefix, resolve

from next.deps import RESERVED_KEYS

from .uid import ORIGIN_FIELD_NAME, URL_NAME_FORM_ACTION, validated_origin_path


if TYPE_CHECKING:
    from django.http import HttpRequest


_ORIGIN_MATCH_ATTR = "_next_form_origin_match"
_UNSET = object()


@dataclass(frozen=True, slots=True)
class OriginMatch:
    """Resolved identity of the page named by a same-site origin URL."""

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


def resolve_url_to_match(
    url: str,
    request: "HttpRequest",
    *,
    filter_reserved: bool = True,
) -> "OriginMatch | None":
    """Resolve a same-site URL against the URLconf to a page identity.

    The URL travels through the same URLconf the request uses, with the
    script prefix stripped. Set `filter_reserved` to keep the captured URL
    kwargs raw when the caller needs every captured parameter rather than
    only the DI-safe ones.
    """
    path = url.partition("?")[0]
    prefix = get_script_prefix()
    if prefix != "/" and path.startswith(prefix):
        path = "/" + path.removeprefix(prefix)
    try:
        match = resolve(path, urlconf=getattr(request, "urlconf", None))
    except Resolver404:
        return None
    kwargs = dict(match.kwargs)
    return OriginMatch(
        page_path=_page_path_from_view(match.func),
        url_kwargs=_filter_reserved_url_kwargs(kwargs) if filter_reserved else kwargs,
        origin=url,
    )


def resolve_url_to_page(url: str, request: "HttpRequest") -> "Path | None":
    """Resolve a URL to the page path of the view that serves it.

    A URL that resolves to a view without a `next_page_path` or that fails
    to resolve returns None.
    """
    match = resolve_url_to_match(url, request)
    return match.page_path if match is not None else None


def _resolve_origin_match(request: "HttpRequest") -> "OriginMatch | None":
    """Resolve the posted origin field against the URLconf."""
    raw = request.POST.get(ORIGIN_FIELD_NAME) if hasattr(request, "POST") else None
    origin = validated_origin_path(raw)
    if origin is None:
        return None
    return resolve_url_to_match(origin, request)


def resolve_origin(request: "HttpRequest") -> "OriginMatch | None":
    """Return the posted-origin match for the request, memoised on the request."""
    cached = getattr(request, _ORIGIN_MATCH_ATTR, _UNSET)
    if cached is not _UNSET:
        return cast("OriginMatch | None", cached)
    match = _resolve_origin_match(request)
    setattr(request, _ORIGIN_MATCH_ATTR, match)
    return match


def _url_kwargs_for_request(request: "HttpRequest") -> dict[str, object]:
    """Return the URL kwargs of the page the request renders or re-renders."""
    match = getattr(request, "resolver_match", None)
    if match is not None and getattr(match, "url_name", None) == URL_NAME_FORM_ACTION:
        origin_match = resolve_origin(request)
        return dict(origin_match.url_kwargs) if origin_match is not None else {}
    if match is not None and getattr(match, "kwargs", None):
        return _filter_reserved_url_kwargs(dict(match.kwargs))
    if getattr(request, "method", None) == "POST":
        origin_match = resolve_origin(request)
        if origin_match is not None:
            return dict(origin_match.url_kwargs)
    return {}


__all__ = [
    "OriginMatch",
    "_url_kwargs_for_request",
    "resolve_origin",
    "resolve_url_to_match",
    "resolve_url_to_page",
]
