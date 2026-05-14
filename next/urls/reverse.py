"""Reverse helpers for file-router page URLs."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.urls import reverse

from next.conf import next_framework_settings

from .manager import app_name
from .parser import default_url_parser


def page_reverse(
    path_template: str = "",
    *,
    namespace: str = app_name,
    **kwargs: object,
) -> str:
    """Reverse a file-router page URL from its directory-tree template."""
    clean_name = default_url_parser.prepare_url_name(path_template)
    full_name = next_framework_settings.URL_NAME_TEMPLATE.format(name=clean_name)
    return reverse(f"{namespace}:{full_name}", kwargs=kwargs or None)


def with_query(base: str, **overrides: object) -> str:
    """Return `base` with its query string updated by `overrides`.

    `None` values drop their key from the result. Multi-valued keys can be
    set by passing a list/tuple value.
    """
    parts = urlsplit(base)
    params = parse_qsl(parts.query, keep_blank_values=True)
    for key in overrides:
        params = [(k, v) for (k, v) in params if k != key]
    pairs: list[tuple[str, str]] = list(params)
    for key, value in overrides.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            pairs.extend((key, str(v)) for v in value)
        else:
            pairs.append((key, str(value)))
    new_query = urlencode(pairs, doseq=False)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment),
    )


__all__ = ["page_reverse", "with_query"]
