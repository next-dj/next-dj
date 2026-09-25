"""The settings tier of the metadata chain and the options beside it."""

import functools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from next.conf import next_framework_settings
from next.conf.signals import settings_reloaded

from .schema import Segment, normalize_metadata


SITE_SOURCE: Final = "NEXT_FRAMEWORK['METADATA']['DEFAULTS']"
"""The source name the settings segment reports in its shape errors."""


@dataclass(frozen=True, slots=True)
class MetadataOptions:
    """The upper-case options of `NEXT_FRAMEWORK["METADATA"]`."""

    noindex: bool = False
    canonical_query: tuple[str, ...] = ()
    checks: Mapping[str, object] = field(default_factory=dict)


def _scope() -> Mapping[str, object]:
    raw = next_framework_settings.METADATA
    return raw if isinstance(raw, Mapping) else {}


def _str_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        return ()
    return tuple(item for item in value if isinstance(item, str))


@functools.cache
def site_segment() -> Segment:
    """Return the settings defaults as the outermost segment of every chain.

    A malformed scope folds to an empty segment here, the system checks report it.
    """
    defaults = _scope().get("DEFAULTS", {})
    if not isinstance(defaults, Mapping):
        return Segment(SITE_SOURCE)
    return normalize_metadata(defaults, source=SITE_SOURCE, site=True)


@functools.cache
def metadata_options() -> MetadataOptions:
    """Return the options read leniently from the metadata scope."""
    scope = _scope()
    checks = scope.get("CHECKS", {})
    return MetadataOptions(
        noindex=bool(scope.get("NOINDEX", False)),
        canonical_query=_str_tuple(scope.get("CANONICAL_QUERY", ())),
        checks=checks if isinstance(checks, Mapping) else {},
    )


def forget_site_defaults(**kwargs) -> None:
    """Drop the memoised settings tier so a reloaded configuration takes effect."""
    site_segment.cache_clear()
    metadata_options.cache_clear()


settings_reloaded.connect(forget_site_defaults)


__all__ = [
    "SITE_SOURCE",
    "MetadataOptions",
    "forget_site_defaults",
    "metadata_options",
    "site_segment",
]
