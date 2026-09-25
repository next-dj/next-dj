"""The memoised read of `NEXT_FRAMEWORK["METADATA"]`, its options and settings tier."""

import functools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from next.conf import next_framework_settings
from next.conf.signals import settings_reloaded

from .normalize import normalize_site_metadata
from .schema import Metadata, Segment


SITE_SOURCE: Final = "NEXT_FRAMEWORK['METADATA']['DEFAULTS']"
"""The source name the settings segment reports in its shape errors."""

METADATA_KEYS: Final = frozenset(
    {"CANONICAL_QUERY", "CHECKS", "DEFAULTS", "NOINDEX", "RENDERER"}
)
"""The keys a `NEXT_FRAMEWORK["METADATA"]` mapping may carry."""

_NO_CHECKS: Final[Mapping[str, object]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class MetadataOptions:
    """The upper-case switches beside `DEFAULTS`, each read leniently."""

    noindex: bool = False
    canonical_query: tuple[str, ...] = ()
    checks: Mapping[str, object] = _NO_CHECKS


def metadata_scope() -> Mapping[str, object]:
    """Return the `METADATA` mapping, empty where the setting holds anything else."""
    raw = next_framework_settings.METADATA
    return raw if isinstance(raw, Mapping) else {}


def _str_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        return ()
    return tuple(item for item in value if isinstance(item, str))


@functools.cache
def metadata_options() -> MetadataOptions:
    """Return the options read leniently from the metadata scope."""
    scope = metadata_scope()
    checks = scope.get("CHECKS")
    return MetadataOptions(
        noindex=bool(scope.get("NOINDEX", False)),
        canonical_query=_str_tuple(scope.get("CANONICAL_QUERY", ())),
        checks=(
            MappingProxyType(dict(checks))
            if isinstance(checks, Mapping)
            else _NO_CHECKS
        ),
    )


@functools.cache
def site_segment() -> Segment:
    """Return the settings defaults as the outermost segment of every chain.

    A malformed scope folds to an empty segment here, the system checks report it.
    """
    defaults = metadata_scope().get("DEFAULTS", {})
    if not isinstance(defaults, Mapping):
        return Segment(SITE_SOURCE)
    return normalize_site_metadata(defaults, source=SITE_SOURCE)


def page_noindex(meta: Metadata) -> bool:
    """Whether the page stays out of the index, by its robots or by `NOINDEX`."""
    return metadata_options().noindex or meta.noindex


def forget_metadata_scope(**kwargs) -> None:
    """Drop the memoised settings tier and options, so a reload takes effect."""
    site_segment.cache_clear()
    metadata_options.cache_clear()


settings_reloaded.connect(forget_metadata_scope)


__all__ = [
    "METADATA_KEYS",
    "SITE_SOURCE",
    "MetadataOptions",
    "forget_metadata_scope",
    "metadata_options",
    "metadata_scope",
    "page_noindex",
    "site_segment",
]
