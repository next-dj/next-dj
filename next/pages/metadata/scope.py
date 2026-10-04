"""The memoised read of `NEXT_FRAMEWORK["METADATA"]`, its options and settings tier."""

import functools
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from django.http import HttpRequest

from next.conf.defaults import DEFAULTS
from next.conf.scopes import scope_value
from next.conf.signals import settings_reloaded
from next.pages.errors import PageMetadataShapeError
from next.site import site_config, site_indexable

from .markers import Metadata, Segment
from .normalize import normalize_site_metadata


SITE_SOURCE: Final = "NEXT_FRAMEWORK['METADATA']['DEFAULTS']"
"""The source name the settings segment reports in its shape errors."""

SITE_NAME_SOURCE: Final = "NEXT_FRAMEWORK['SITE']['NAME']"
"""The source a `site_name` read off the site scope rather than `DEFAULTS` names."""

METADATA_KEYS: Final = frozenset(DEFAULTS["METADATA"])
"""The keys a `NEXT_FRAMEWORK["METADATA"]` mapping may carry."""

logger = logging.getLogger(__name__)


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


@functools.cache
def site_segment() -> Segment:
    """Return the settings defaults as the outermost segment of every chain.

    The site name defaults to `SITE["NAME"]`. A malformed `DEFAULTS` is logged once per
    reload and contributes nothing, since raising would fail every render.
    """
    defaults = scope_value("METADATA", "DEFAULTS")
    segment = Segment(SITE_SOURCE)
    if isinstance(defaults, Mapping):
        try:
            segment = normalize_site_metadata(defaults, source=SITE_SOURCE)
        except PageMetadataShapeError as exc:
            logger.warning("%s, so the defaults fold to nothing", exc)
    name = site_config().name
    if segment.metadata.site_name is None and name is not None:
        segment = replace(segment, metadata=replace(segment.metadata, site_name=name))
    return segment


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
    site_segment.cache_clear()
    metadata_options.cache_clear()


settings_reloaded.connect(forget_metadata_scope)


__all__ = [
    "METADATA_KEYS",
    "SITE_NAME_SOURCE",
    "SITE_SOURCE",
    "MetadataOptions",
    "forget_metadata_scope",
    "metadata_options",
    "noindexed",
    "site_name_source",
    "site_segment",
]
