"""Reverse helpers for file-router page URLs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.urls import reverse
from django.utils.functional import lazy

from next.conf import next_framework_settings

from .manager import app_name
from .parser import default_url_parser


if TYPE_CHECKING:
    from collections.abc import Mapping


def page_reverse(
    path_template: str = "", /, *, namespace: str = app_name, **kwargs
) -> str:
    """Reverse a file-router page URL from its directory-tree template."""
    return page_reverse_in(namespace, path_template, kwargs)


def page_reverse_in(
    namespace: str, path_template: str, kwargs: Mapping[str, Any]
) -> str:
    """Reverse a page URL in `namespace`, a URL kwarg named `namespace` included."""
    clean_name = default_url_parser.prepare_url_name(path_template)
    full_name = next_framework_settings.URL_NAME_TEMPLATE.format(name=clean_name)
    return reverse(f"{namespace}:{full_name}", kwargs=dict(kwargs) or None)


# Lazy variant for values read before the URLconf is ready, like `Meta.success_url`.
page_reverse_lazy = lazy(page_reverse, str)


def with_query(base: str, **overrides) -> str:
    """Return `base` with its query string updated by `overrides`.

    A `None` or empty list drops its key and a non-empty one sets a multi-value key.
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
        (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment)
    )


__all__ = ["page_reverse", "page_reverse_in", "page_reverse_lazy", "with_query"]
