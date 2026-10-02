"""The site identity `NEXT_FRAMEWORK["SITE"]` declares, read once per reload."""

import enum
import functools
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.sites.requests import RequestSite
from django.contrib.sites.shortcuts import get_current_site
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from django.http import HttpRequest
from django.http.request import split_domain_port
from django.utils.functional import Promise

from next.conf.defaults import DEFAULTS
from next.conf.imports import import_callable
from next.conf.scopes import scope_value
from next.conf.settings import fail_loudly
from next.conf.signals import settings_reloaded
from next.diagnostics import FailureLog
from next.introspect import describe_callable
from next.utils import UNSET, WEB_SCHEMES, Unset

from .errors import SiteOriginError


SITE_KEYS: Final = frozenset(DEFAULTS["SITE"])
"""The keys a `NEXT_FRAMEWORK["SITE"]` mapping may carry."""

SITE_ORIGIN_ATTR: Final = "_next_site_origin"
"""The request attribute the origin of one request is held under."""

_DECLARED_ATTR: Final = "_next_site_declared"
_URL_SETTING: Final = "NEXT_FRAMEWORK['SITE']['URL']"
_INDEXABLE_SETTING: Final = "NEXT_FRAMEWORK['SITE']['INDEXABLE']"

_ROOT_PATHS: Final = frozenset({"", "/"})

type SiteUrlRule = Callable[[HttpRequest | None], object]
type IndexableRule = bool | Callable[[HttpRequest | None], object]

logger = logging.getLogger(__name__)
_failures = FailureLog(logger)


class _Failed(enum.Enum):
    """What a callable `URL` that raised or answered no origin declares."""

    FAILED = enum.auto()


type _Declared = tuple[str, str] | Literal[_Failed.FAILED] | None


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

    A path, a query, a fragment or credentials would leak into every URL built on it,
    so none passes, and neither does a host Django would refuse or a port out of range.
    """
    try:
        parts = urlsplit(value)
        # The port is parsed lazily and raises on one out of range.
        _port = parts.port
    except ValueError:
        return None
    if (
        parts.scheme not in WEB_SCHEMES
        or not parts.netloc
        or parts.username is not None
        or parts.password is not None
        or not split_domain_port(parts.netloc)[0]
        or parts.path not in _ROOT_PATHS
        or parts.query
        or parts.fragment
    ):
        return None
    return parts.scheme, parts.netloc


def url_rule(value: object) -> str | SiteUrlRule | None:
    """Return the `URL` rule a setting value names, `None` for one the scope drops.

    The system check reads the same answer, so the two never disagree on a value.
    """
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
        url=url_rule(scope_value("SITE", "URL")),
        name=name if isinstance(name, str | Promise) else None,
        indexable=_indexable_rule(scope_value("SITE", "INDEXABLE")),
    )


def forget_site_config(**kwargs) -> None:
    """Drop the memoised site identity, so a settings reload takes effect."""
    site_config.cache_clear()


settings_reloaded.connect(forget_site_config)


def _url_failed(key: str, message: str, exc: Exception | None = None) -> _Failed:
    """Report a callable `URL` that failed, raising under `DEBUG`, else logging once."""
    if fail_loudly():
        raise ImproperlyConfigured(message) from exc
    if exc is None:
        _failures.warn(("URL", key), message)
    else:
        _failures.contain(exc, ("URL", key), message)
    return _Failed.FAILED


def _answered_origin(rule: SiteUrlRule, request: HttpRequest | None) -> _Declared:
    """Return the origin a callable `URL` answers, `None` when it declines one."""
    try:
        value = rule(request)
    except Exception as exc:  # noqa: BLE001 - the callable is project code
        return _url_failed(
            "raised",
            f"{_URL_SETTING} {describe_callable(rule)} raised "
            f"{type(exc).__name__}, so the request has no declared origin and the "
            "SEO routes answer 503. Make it answer an http or https origin, or "
            "None to fall back to the request host.",
            exc,
        )
    if value is None:
        return None
    origin = url_origin(value) if isinstance(value, str) else None
    if origin is None:
        return _url_failed(
            "answer",
            f"{_URL_SETTING} {describe_callable(rule)} answered {value!r}, which is "
            "no http or https origin, so the SEO routes answer 503. Answer an "
            "origin with no path like 'https://acme.example', or None to fall back "
            "to the request host.",
        )
    return origin


def _declared_origin(request: HttpRequest | None) -> _Declared:
    """Return the origin the `URL` rule answers, `None` for no rule or no origin.

    A callable answers once per request, so its failure is reported once as well.
    """
    rule = site_config().url
    if rule is None:
        return None
    if isinstance(rule, str):
        return url_origin(rule)
    if request is None:
        return _answered_origin(rule, None)
    held: _Declared | Unset = getattr(request, _DECLARED_ATTR, UNSET)
    if held is UNSET:
        held = _answered_origin(rule, request)
        setattr(request, _DECLARED_ATTR, held)
    return held


def site_url(request: HttpRequest | None = None) -> str | None:
    """Return the origin the site URL declares, `None` where it declares none.

    A callable `URL` answers per request, so one process can serve several tenants.
    """
    origin = _declared_origin(request)
    return f"{origin[0]}://{origin[1]}" if isinstance(origin, tuple) else None


def site_url_failed(request: HttpRequest | None) -> bool:
    """Whether a callable `URL` raised or answered no origin for `request`.

    A page falls back to the request host, while the SEO routes answer 503 instead.
    """
    return _declared_origin(request) is _Failed.FAILED


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
        if not isinstance(declared, tuple):
            raise SiteOriginError
        return declared
    held = getattr(request, SITE_ORIGIN_ATTR, None)
    if isinstance(held, tuple):
        return held
    declared = _declared_origin(request)
    origin = (
        declared
        if isinstance(declared, tuple)
        else (request.scheme or "https", _current_domain(request))
    )
    setattr(request, SITE_ORIGIN_ATTR, origin)
    return origin


def site_indexable(request: HttpRequest | None = None) -> bool:
    """Whether search engines may index the site, the answer every head tag reads.

    A callable that raises closes the site for the request, so a failure never opens
    a preview host to search.
    """
    rule = site_config().indexable
    if rule is None:
        return not settings.DEBUG
    if isinstance(rule, bool):
        return rule
    try:
        return bool(rule(request))
    except Exception as exc:  # noqa: BLE001 - the callable is project code
        _failures.contain(
            exc,
            "INDEXABLE",
            "%s %s raised, so the site reads as closed to search and every response "
            "carries noindex. Make it answer a bool for a request and for None.",
            _INDEXABLE_SETTING,
            describe_callable(rule),
        )
        return False


def debug_closed() -> bool:
    """Whether only `DEBUG` closes the site, under the `"auto"` rule of `INDEXABLE`."""
    return site_config().indexable is None and bool(settings.DEBUG)


def site_closed_to_crawlers(request: HttpRequest | None) -> bool:
    """Whether the SEO routes serve the closed documents for `request`.

    A site only `DEBUG` closes still serves its own, previewed under noindex.
    """
    return not site_indexable(request) and not debug_closed()


def indexable_without_request() -> bool:
    """Whether the site reads as open where no request exists, as in a system check.

    A callable rule is never called there and reads as open, so every check runs.
    """
    rule = site_config().indexable
    if rule is None:
        return not settings.DEBUG
    return rule if isinstance(rule, bool) else True


__all__ = [
    "SITE_KEYS",
    "SITE_ORIGIN_ATTR",
    "SiteConfig",
    "debug_closed",
    "forget_site_config",
    "indexable_without_request",
    "is_url_literal",
    "site_closed_to_crawlers",
    "site_config",
    "site_indexable",
    "site_origin",
    "site_url",
    "site_url_failed",
    "url_origin",
    "url_rule",
]
