"""HTTP request parsing utilities for form dispatch."""

from typing import TYPE_CHECKING

from next.deps import RESERVED_KEYS


if TYPE_CHECKING:
    from django.http import HttpRequest


def _filter_reserved_url_kwargs(url_kwargs: dict[str, object]) -> dict[str, object]:
    """Drop keys that collide with DI names used by `resolve_dependencies`."""
    return {k: v for k, v in url_kwargs.items() if k not in RESERVED_KEYS}


def _url_kwargs_from_post(request: "HttpRequest") -> dict[str, object]:
    """Parse `_url_param_*` hidden fields from POST."""
    out: dict[str, object] = {}
    if not hasattr(request, "POST"):
        return out
    for key, value in request.POST.items():
        if not key.startswith("_url_param_"):
            continue
        param_name = key[len("_url_param_") :]
        if param_name in RESERVED_KEYS:
            continue
        if isinstance(value, str):
            try:
                out[param_name] = int(value)
            except ValueError:
                out[param_name] = value
        else:
            out[param_name] = value
    return out


def _url_kwargs_from_resolver_or_post(request: "HttpRequest") -> dict[str, object]:
    """Return URL kwargs from the resolver match, otherwise from POST hidden fields."""
    resolver_match = getattr(request, "resolver_match", None)
    if resolver_match and getattr(resolver_match, "kwargs", None):
        return _filter_reserved_url_kwargs(dict(resolver_match.kwargs))
    if getattr(request, "method", None) == "POST" and hasattr(request, "POST"):
        return _url_kwargs_from_post(request)
    return {}


__all__ = [
    "_filter_reserved_url_kwargs",
    "_url_kwargs_from_post",
    "_url_kwargs_from_resolver_or_post",
]
