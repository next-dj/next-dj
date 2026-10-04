"""The memoised read of `NEXT_FRAMEWORK["METADATA"]`, its options and settings tier."""

import functools
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final, NamedTuple

from django.http import HttpRequest

from next.conf.defaults import DEFAULTS
from next.conf.scopes import scope_value
from next.conf.settings import fail_loudly
from next.conf.signals import settings_reloaded
from next.diagnostics import FailureLog, mark_degraded
from next.pages.errors import PageMetadataShapeError
from next.site import site_indexable
from next.site.config import site_config

from .markers import REFUSED_ROBOTS, Metadata, Segment
from .normalize import normalize_site_metadata


SITE_SOURCE: Final = "NEXT_FRAMEWORK['METADATA']['DEFAULTS']"
"""The source name the settings segment reports in its shape errors."""

SITE_NAME_SOURCE: Final = "NEXT_FRAMEWORK['SITE']['NAME']"
"""The source a `site_name` read off the site scope rather than `DEFAULTS` names."""

METADATA_KEYS: Final = frozenset(DEFAULTS["METADATA"])
"""The keys a `NEXT_FRAMEWORK["METADATA"]` mapping may carry."""

_failures: Final = FailureLog(logging.getLogger(__name__))


@dataclass(frozen=True, slots=True)
class MetadataOptions:
    """The upper-case options of the `METADATA` scope, each read leniently."""

    canonical_query: tuple[str, ...] = ()


def _str_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        return ()
    return tuple(item for item in value if isinstance(item, str))


@functools.cache
def metadata_options() -> MetadataOptions:
    """Return the options read leniently from the metadata scope."""
    return MetadataOptions(
        canonical_query=_str_tuple(scope_value("METADATA", "CANONICAL_QUERY"))
    )


class SiteTier(NamedTuple):
    """The settings segment and the shape error that refused `DEFAULTS`, if any."""

    segment: Segment
    refusal: PageMetadataShapeError | None = None


@functools.cache
def site_tier() -> SiteTier:
    """Return the settings defaults as the outermost segment of every chain.

    The site name defaults to `SITE["NAME"]`. A malformed `DEFAULTS` raises under
    `DEBUG` or `STRICT_LOADING`, as a malformed `page.py` dict does. Otherwise the
    segment carries the site name alone under `REFUSED_ROBOTS`, with the refusal, so
    a page is not indexed with defaults it was not meant to have.
    """
    defaults = scope_value("METADATA", "DEFAULTS")
    tier = SiteTier(Segment(SITE_SOURCE))
    if isinstance(defaults, Mapping):
        try:
            tier = SiteTier(normalize_site_metadata(defaults, source=SITE_SOURCE))
        except PageMetadataShapeError as exc:
            if fail_loudly():
                raise
            refused = Segment(SITE_SOURCE, Metadata(robots=REFUSED_ROBOTS))
            tier = SiteTier(refused, exc)
    segment = tier.segment
    name = site_config().name
    if segment.metadata.site_name is None and name is not None:
        segment = replace(segment, metadata=replace(segment.metadata, site_name=name))
    return tier._replace(segment=segment)


def site_segment() -> Segment:
    """Return the segment of `site_tier`, memoised until the settings reload."""
    return site_tier().segment


def contain_site_refusal() -> None:
    """Mark the current render degraded while `DEFAULTS` is refused.

    A render that reads a refused tier is not stored in a shared cache, and the
    refusal is logged at the `FailureLog` rate.
    """
    refusal = site_tier().refusal
    if refusal is None:
        return
    mark_degraded()
    _failures.warn(
        SITE_SOURCE,
        "%s, so every page renders under noindex without the defaults. Run "
        "manage.py check to see what to fix.",
        refusal,
    )


def site_name_source() -> str:
    """Return the setting the `site_name` of the settings tier comes from."""
    defaults = scope_value("METADATA", "DEFAULTS")
    declared = isinstance(defaults, Mapping) and defaults.get("site_name") is not None
    return SITE_SOURCE if declared else SITE_NAME_SOURCE


def noindexed(meta: Metadata, *, request: HttpRequest | None = None) -> bool:
    """Whether a page stays out of the index, by the site rule or its own robots."""
    return not site_indexable(request) or meta.noindex


def forget_metadata_scope(**kwargs) -> None:
    """Drop the memoised settings tier and options, so a reload takes effect."""
    site_tier.cache_clear()
    metadata_options.cache_clear()


settings_reloaded.connect(forget_metadata_scope)


__all__ = [
    "METADATA_KEYS",
    "SITE_NAME_SOURCE",
    "SITE_SOURCE",
    "MetadataOptions",
    "SiteTier",
    "contain_site_refusal",
    "forget_metadata_scope",
    "metadata_options",
    "noindexed",
    "site_name_source",
    "site_segment",
    "site_tier",
]
