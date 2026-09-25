"""System checks for the `METADATA` scope and its `DEFAULTS` tier.

The ids are `next.E035`, `next.E098` and the ones `segment_errors` names.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, register

from next.checks import NEXT
from next.checks.common import errors_for_unknown_keys
from next.conf.defaults import USER_SETTING
from next.pages.errors import PageMetadataShapeError
from next.pages.metadata import SITE_SOURCE, normalize_site_metadata
from next.pages.metadata.scope import METADATA_KEYS

from .links import is_origin


if TYPE_CHECKING:
    from next.pages.metadata import Segment


_SCOPE_PREFIX: Final = "NEXT_FRAMEWORK['METADATA']"


def raw_metadata_scope() -> dict[str, Any] | None:
    """Return the raw `METADATA` scope, or `None` where `next.E076` reports it."""
    raw = getattr(settings, USER_SETTING, None)
    if not isinstance(raw, dict):
        return None
    scope = raw.get("METADATA")
    return scope if isinstance(scope, dict) else None


def site_defaults(scope: dict[str, Any]) -> tuple[Segment | None, CheckMessage | None]:
    """Normalise the raw `DEFAULTS`, answering the `next.E098` it earns instead."""
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


def segment_errors(segment: Segment, *, source: str, obj: object) -> list[CheckMessage]:
    """Return `next.E100`, `next.E101` and `next.E105` for one normalised segment."""
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
    if segment.base is not None and not is_origin(segment.base):
        errors.append(
            Error(
                f"{source} declares base {segment.base!r}, which is not an origin. "
                "Write an absolute http or https URL with a host and no path, "
                "query or fragment, like 'https://acme.example'.",
                obj=obj,
                id="next.E101",
            )
        )
    return errors


@register(Tags.templates, NEXT)
def check_metadata_settings_scope(*args, **kwargs) -> list[CheckMessage]:
    """Validate the `METADATA` options, and the `DEFAULTS` tier like a page."""
    scope = raw_metadata_scope()
    if scope is None:
        return []
    errors = errors_for_unknown_keys(scope, allowed=METADATA_KEYS, prefix=_SCOPE_PREFIX)
    segment, error = site_defaults(scope)
    if error is not None:
        errors.append(error)
    elif segment is not None:
        errors.extend(segment_errors(segment, source=SITE_SOURCE, obj=settings))
    return errors


__all__ = [
    "check_metadata_settings_scope",
    "raw_metadata_scope",
    "segment_errors",
    "site_defaults",
]
