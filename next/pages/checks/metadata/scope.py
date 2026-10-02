"""System checks for the `METADATA` scope and its `DEFAULTS` tier."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final, NamedTuple, cast

from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)
from django.core.exceptions import ImproperlyConfigured

from next.checks import NEXT, SEO
from next.checks.common import RunMemo, errors_for_unknown_keys, raw_scope
from next.pages.errors import PageMetadataShapeError
from next.pages.metadata.backends import configured_renderer_class
from next.pages.metadata.ld import raw_id
from next.pages.metadata.normalize import normalize_site_metadata
from next.pages.metadata.scope import METADATA_KEYS, SITE_SOURCE


if TYPE_CHECKING:
    from next.pages.metadata.markers import Segment

    from .pages import MetadataPage


_SCOPE_PREFIX: Final = "NEXT_FRAMEWORK['METADATA']"


def raw_metadata_scope() -> dict[str, Any] | None:
    """Return the raw `METADATA` scope, or `None` where `next.E076` reports it."""
    return cast("dict[str, Any] | None", raw_scope("METADATA"))


type _SiteDefaults = tuple["Segment | None", CheckMessage | None]

_site_defaults: RunMemo[_SiteDefaults] = RunMemo()


def site_defaults(scope: dict[str, Any]) -> _SiteDefaults:
    """Normalise the raw `DEFAULTS` once per check run, or answer its `next.E098`."""
    return _site_defaults.get(scope, lambda: _normalised_defaults(scope))


def _normalised_defaults(scope: dict[str, Any]) -> _SiteDefaults:
    defaults = scope.get("DEFAULTS", {})
    if not isinstance(defaults, Mapping):
        return None, Error(
            f"{SITE_SOURCE} must be a mapping of metadata keys, got "
            f"{type(defaults).__name__!r}. The chain ignores it as written.",
            obj=settings,
            id="next.E098",
        )
    try:
        return normalize_site_metadata(defaults, source=SITE_SOURCE), None
    except PageMetadataShapeError as exc:
        return None, Error(
            f"{exc}. The keys of DEFAULTS are the lower-case metadata keys a "
            "page.py declares, and the title takes only the template and "
            "default form.",
            obj=settings,
            id="next.E098",
        )


class DeclaredSegment(NamedTuple):
    """One segment a source declares, with the object a check reports it on."""

    source: str
    obj: object
    segment: Segment


def declared_segments(pages: list[MetadataPage]) -> list[DeclaredSegment]:
    """Return the settings segment and the own dict of every page that normalised."""
    scope = raw_metadata_scope()
    site = None if scope is None else site_defaults(scope)[0]
    found = [] if site is None else [DeclaredSegment(SITE_SOURCE, settings, site)]
    found.extend(
        DeclaredSegment(str(entry.page_path), str(entry.page_path), entry.segment)
        for entry in pages
        if entry.segment is not None
    )
    return found


def duplicate_ids(segment: Segment) -> list[str]:
    """Return the JSON-LD `@id` values one segment declares more than once."""
    counts = Counter(
        ident for item in segment.metadata.jsonld if (ident := raw_id(item)) is not None
    )
    return sorted(ident for ident, count in counts.items() if count > 1)


def segment_errors(segment: Segment, *, source: str, obj: object) -> list[CheckMessage]:
    """Return `next.E100`, `next.E105` and `next.W103` for one normalised segment."""
    errors: list[CheckMessage] = []
    spec = segment.title
    if spec is not None and spec.template is not None and spec.default is None:
        errors.append(
            Error(
                f"{source} declares a title template without a default, so a page "
                "without a title of its own renders none. Add title.default.",
                obj=obj,
                id="next.E100",
            )
        )
    if spec is not None and any(
        isinstance(value, str) and not value
        for value in (spec.text, spec.default, spec.absolute)
    ):
        errors.append(
            Error(
                f"{source} declares an empty title, which renders an empty <title>. "
                "Give the title text, or drop the key so the chain default applies.",
                obj=obj,
                id="next.E105",
            )
        )
    duplicates = duplicate_ids(segment)
    if duplicates:
        errors.append(
            DjangoWarning(
                f"{source} declares several JSON-LD objects with the @id "
                f"{', '.join(map(repr, duplicates))}, and only the last one "
                "renders. Merge them into one object.",
                obj=obj,
                id="next.W103",
            )
        )
    return errors


def renderer_errors() -> list[CheckMessage]:
    """Return `next.E107` when `METADATA["RENDERER"]` names no concrete renderer.

    Resolving the path imports its module, and the class is never instantiated.
    """
    try:
        configured_renderer_class()
    except ImproperlyConfigured as exc:
        return [
            Error(
                f"{exc} Every page falls back to HtmlMetadataRenderer and logs it. "
                "Name a concrete subclass of next.pages.MetadataRenderer, or drop "
                "the key for the default.",
                obj=settings,
                id="next.E107",
            )
        ]
    return []


@register(Tags.templates, NEXT, SEO)
def check_metadata_settings_scope(*args, **kwargs) -> list[CheckMessage]:
    """Validate the `METADATA` options, and the `DEFAULTS` tier like a page.

    A `Replace` in `DEFAULTS` has nothing to replace and earns `next.W104`.
    """
    scope = raw_metadata_scope()
    if scope is None:
        return []
    errors = errors_for_unknown_keys(scope, allowed=METADATA_KEYS, prefix=_SCOPE_PREFIX)
    errors.extend(renderer_errors())
    segment, error = site_defaults(scope)
    if error is not None:
        errors.append(error)
    elif segment is not None:
        errors.extend(segment_errors(segment, source=SITE_SOURCE, obj=settings))
        if segment.replaced:
            keys = ", ".join(sorted(segment.replaced))
            errors.append(
                DjangoWarning(
                    f"{SITE_SOURCE} wraps {keys} in Replace or RESET, which has no "
                    "inherited value to replace there. Write the value itself.",
                    obj=settings,
                    id="next.W104",
                )
            )
    return errors


__all__ = [
    "DeclaredSegment",
    "check_metadata_settings_scope",
    "declared_segments",
    "duplicate_ids",
    "raw_metadata_scope",
    "segment_errors",
    "site_defaults",
]
