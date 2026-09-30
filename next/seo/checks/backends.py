"""System checks for `NEXT_FRAMEWORK["SEO"]` and its sitemap backends."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from django.conf import settings
from django.core.checks import CheckMessage, Error, register
from django.core.exceptions import ImproperlyConfigured

from next.backends import resolve_backend_class
from next.checks import NEXT, SEO
from next.checks.common import errors_for_unknown_keys
from next.conf.defaults import USER_SETTING
from next.seo.backends import SitemapBackend
from next.seo.manager import SEO_KEYS, SEO_SCOPE, SITEMAP_BACKENDS


_PREFIX: Final = f"NEXT_FRAMEWORK[{SEO_SCOPE!r}]"


def _raw_seo_scope() -> Mapping[str, object] | None:
    """Return the raw `SEO` scope, or `None` where `next.E076` reports it."""
    raw = getattr(settings, USER_SETTING, None)
    if not isinstance(raw, dict):
        return None
    scope = raw.get(SEO_SCOPE)
    return scope if isinstance(scope, dict) else None


def _seo_error(message: str) -> CheckMessage:
    return Error(message, obj=settings, id="next.E120")


def _entry_errors(index: int, entry: object) -> list[CheckMessage]:
    """Return `next.E120` when one `SITEMAP_BACKENDS` entry names no usable backend."""
    where = f"{_PREFIX}[{SITEMAP_BACKENDS!r}][{index}]"
    if not isinstance(entry, Mapping):
        return [_seo_error(f"{where} is {entry!r}, expected a mapping with BACKEND.")]
    try:
        resolve_backend_class(entry, base=SitemapBackend)
    except (ImproperlyConfigured, ImportError) as exc:
        return [
            _seo_error(
                f"{where} names no usable sitemap backend ({exc}). Name a "
                "concrete next.seo.SitemapBackend subclass by its dotted path."
            )
        ]
    options = entry.get("OPTIONS", {})
    if not isinstance(options, Mapping):
        return [_seo_error(f"{where}['OPTIONS'] is {options!r}, expected a mapping.")]
    return []


@register(NEXT, SEO)
def check_seo_settings(*args, **kwargs) -> list[CheckMessage]:
    """Validate the `SEO` keys (`next.E035`) and its sitemap backends (`next.E120`)."""
    scope = _raw_seo_scope()
    if scope is None:
        return []
    errors = errors_for_unknown_keys(dict(scope), allowed=SEO_KEYS, prefix=_PREFIX)
    if SITEMAP_BACKENDS not in scope:
        return errors
    entries = scope[SITEMAP_BACKENDS]
    if not isinstance(entries, list):
        errors.append(
            _seo_error(
                f"{_PREFIX}[{SITEMAP_BACKENDS!r}] is {entries!r}, expected a list of "
                "backend entries. The default page-tree backend answers instead."
            )
        )
        return errors
    for index, entry in enumerate(entries):
        errors.extend(_entry_errors(index, entry))
    return errors


__all__ = ["check_seo_settings"]
