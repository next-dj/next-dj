"""The site identity `NEXT_FRAMEWORK["SITE"]` declares, read once per reload."""

import functools
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.sites.requests import RequestSite
from django.contrib.sites.shortcuts import get_current_site
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpRequest
from django.utils.functional import Promise

from next.conf.defaults import DEFAULTS
from next.conf.imports import import_callable
from next.conf.scopes import scope_value
from next.conf.signals import settings_reloaded
from next.utils import WEB_SCHEMES

from .errors import SiteOriginError


SITE_KEYS: Final = frozenset(DEFAULTS["SITE"])
"""The keys a `NEXT_FRAMEWORK["SITE"]` mapping may carry."""

SITE_ORIGIN_ATTR: Final = "_next_site_origin"
"""The request attribute the origin of one request is held under."""

_ROOT_PATHS: Final = frozenset({"", "/"})

type SiteUrlRule = Callable[[HttpRequest | None], object]
type IndexableRule = bool | Callable[[HttpRequest | None], object]


@dataclass(frozen=True, slots=True)
class SiteConfig:
    """The `SITE` scope read leniently, the system checks reporting what it drops.

    `indexable` is `None` for the `"auto"` rule, which reads `DEBUG` on every call.
    """

    url: str | SiteUrlRule | None = None
    name: str | Promise | None = None
    indexable: IndexableRule | None = None


def is_url_literal(value: str) -> bool:
    """Whether a `URL` string is an address, not the dotted path of a callable."""
    return "://" in value


@functools.lru_cache(maxsize=256)
def url_origin(value: str) -> tuple[str, str] | None:
    """Return the scheme and the host of an http or https origin, `None` for the rest.

    A path, a query or a fragment would leak into every URL built on it, so none passes.
    """
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if (
        parts.scheme not in WEB_SCHEMES
        or not parts.netloc
        or parts.path not in _ROOT_PATHS
        or parts.query
        or parts.fragment
    ):
        return None
    return parts.scheme, parts.netloc


def _url_rule(value: object) -> str | SiteUrlRule | None:
    if isinstance(value, str):
        if is_url_literal(value):
            return value if url_origin(value) is not None else None
        return import_callable(value) if value else None
    return value if callable(value) else None


def _indexable_rule(value: object) -> IndexableRule | None:
    return value if isinstance(value, bool) or callable(value) else None


@functools.cache
def site_config() -> SiteConfig:
    """Return the site identity read from the settings, built once per reload."""
    name = scope_value("SITE", "NAME")
    return SiteConfig(
        url=_url_rule(scope_value("SITE", "URL")),
        name=name if isinstance(name, str | Promise) else None,
        indexable=_indexable_rule(scope_value("SITE", "INDEXABLE")),
    )


def forget_site_config(**kwargs) -> None:
    """Drop the memoised site identity, so a settings reload takes effect."""
    site_config.cache_clear()


settings_reloaded.connect(forget_site_config)


def _declared_origin(request: HttpRequest | None) -> tuple[str, str] | None:
    """Return the origin the `URL` rule answers, `None` for no rule or no origin."""
    rule = site_config().url
    if rule is None:
        return None
    value = rule if isinstance(rule, str) else rule(request)
    return url_origin(value) if isinstance(value, str) else None


def site_url(request: HttpRequest | None = None) -> str | None:
    """Return the origin the site URL declares, `None` where it declares none.

    A callable `URL` answers per request, so one process can serve several tenants.
    """
    origin = _declared_origin(request)
    return None if origin is None else f"{origin[0]}://{origin[1]}"


def _current_domain(request: HttpRequest) -> str:
    """Return the domain of the current site, the request host where the table has none.

    Without `SITE_ID` an unknown host finds no row, which is no reason to answer 500.
    """
    try:
        return str(get_current_site(request).domain)
    except ObjectDoesNotExist:
        return RequestSite(request).domain


def site_origin(request: HttpRequest | None) -> tuple[str, str]:
    """Return the scheme and the host every absolute URL of one request is built on.

    The site URL wins, then the current site of `request`, a sites row or its `Host`.
    """
    if request is None:
        declared = _declared_origin(None)
        if declared is None:
            raise SiteOriginError
        return declared
    held = getattr(request, SITE_ORIGIN_ATTR, None)
    if isinstance(held, tuple):
        return held
    origin = _declared_origin(request) or (
        request.scheme or "https",
        _current_domain(request),
    )
    setattr(request, SITE_ORIGIN_ATTR, origin)
    return origin


def site_indexable(request: HttpRequest | None = None) -> bool:
    """Whether search engines may index the site, the answer every head tag reads."""
    rule = site_config().indexable
    if rule is None:
        return not settings.DEBUG
    if isinstance(rule, bool):
        return rule
    return bool(rule(request))


def debug_closed() -> bool:
    """Whether only `DEBUG` closes the site, under the `"auto"` rule of `INDEXABLE`."""
    return site_config().indexable is None and bool(settings.DEBUG)


__all__ = [
    "SITE_KEYS",
    "SITE_ORIGIN_ATTR",
    "SiteConfig",
    "debug_closed",
    "forget_site_config",
    "is_url_literal",
    "site_config",
    "site_indexable",
    "site_origin",
    "site_url",
    "url_origin",
]
