"""System checks for `NEXT_FRAMEWORK["SITE"]`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from django.apps import apps
from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register
from django.utils.functional import Promise

from next.checks import NEXT, SEO
from next.checks.common import errors_for_unknown_keys, raw_scope, takes_request
from next.conf.defaults import AUTO
from next.introspect import describe_callable

from .config import SITE_KEYS, url_rule


if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any


_SCOPE: Final = "SITE"
_PREFIX: Final = f"NEXT_FRAMEWORK[{_SCOPE!r}]"
_SITES_APP: Final = "django.contrib.sites"


def _url_usable(value: object) -> bool:
    return value is None or url_rule(value) is not None


def _indexable_usable(value: object) -> bool:
    return value == AUTO or isinstance(value, bool) or callable(value)


def _signature_error(key: str, func: Callable[..., Any]) -> CheckMessage:
    """Return `next.E129` for a callable that cannot take the request alone."""
    return Error(
        f"{_PREFIX}[{key!r}] is {describe_callable(func)}, which cannot be called "
        "with the request as its one positional argument, so every call raises. "
        "Give it one parameter for the request, which is None outside a request.",
        obj=settings,
        id="next.E129",
    )


def _site_error(key: str, value: object, expected: str) -> CheckMessage:
    return Error(
        f"{_PREFIX}[{key!r}] is {value!r}, which the site scope cannot use. "
        f"Write {expected}.",
        obj=settings,
        id="next.E129",
    )


@register(NEXT, SEO)
def check_site_settings(*args, **kwargs) -> list[CheckMessage]:
    """Validate the `SITE` values (`next.E129`) and its keys (`next.E035`)."""
    scope = raw_scope(_SCOPE)
    if scope is None:
        return []
    errors = errors_for_unknown_keys(dict(scope), allowed=SITE_KEYS, prefix=_PREFIX)
    url = scope.get("URL")
    rule = url_rule(url)
    if callable(rule) and not takes_request(rule):
        errors.append(_signature_error("URL", rule))
    elif not _url_usable(url):
        errors.append(
            _site_error(
                "URL",
                url,
                "an http or https origin with no path like 'https://acme.example', "
                "a callable taking the request, or the dotted path of one",
            )
        )
    name = scope.get("NAME")
    if name is not None and not isinstance(name, str | Promise):
        errors.append(_site_error("NAME", name, "the site name as text"))
    indexable = scope.get("INDEXABLE", AUTO)
    if callable(indexable) and not takes_request(indexable):
        errors.append(_signature_error("INDEXABLE", indexable))
    elif not _indexable_usable(indexable):
        errors.append(
            _site_error(
                "INDEXABLE",
                indexable,
                "'auto', a bool, or a callable taking the request",
            )
        )
    return errors


def _site_row_pinned() -> bool:
    """Whether `django.contrib.sites` answers one row whatever the `Host` header is."""
    return (
        apps.is_installed(_SITES_APP) and getattr(settings, "SITE_ID", None) is not None
    )


@register(NEXT, SEO, deploy=True)
def check_site_url_for_deploy(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a deployed site builds its absolute URLs from `Host` (W119, W132)."""
    scope = raw_scope(_SCOPE) or {}
    if scope.get("URL") is not None or _site_row_pinned():
        return []
    if "*" in getattr(settings, "ALLOWED_HOSTS", ()):
        return [
            DjangoWarning(
                f"{_PREFIX}['URL'] is not set and ALLOWED_HOSTS holds '*', so any "
                "client picks the host of the canonical, Open Graph, sitemap and "
                "robots URLs through the Host header, and a CDN may keep them for "
                "everyone. Set the origin, like 'https://acme.example', or list the "
                "real hosts.",
                obj=settings,
                id="next.W132",
            )
        ]
    return [
        DjangoWarning(
            f"{_PREFIX}['URL'] is not set, so canonical, Open Graph, sitemap and "
            "robots URLs follow the Host header of each request. Set the origin "
            "crawlers and social cards should see, like 'https://acme.example', or "
            "pin a django.contrib.sites row with SITE_ID.",
            obj=settings,
            id="next.W119",
        )
    ]


__all__ = ["check_site_settings", "check_site_url_for_deploy"]
