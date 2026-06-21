"""Partial-request headers, intent parsing, and Vary stamping."""

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from django.utils.cache import patch_vary_headers


if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponseBase


REQUEST_FLAG = "X-Next-Request"
ZONE = "X-Next-Zone"
VALIDATE = "X-Next-Validate"
MERGE = "X-Next-Merge"
VERSION = "X-Next-Version"
REQUEST_ID = "X-Next-Request-Id"
ORIGIN = "X-Next-Origin"

RESPONSE_VERSION = "X-Next-Version"
RESPONSE_FORM = "X-Next-Form"
RESPONSE_ACTION = "X-Next-Action"

CONTENT_TYPE = "application/vnd.next.patches+json"
ACCEPT = "application/vnd.next.patches+json, text/html;q=0.9"

VARY_HEADERS: tuple[str, ...] = (REQUEST_FLAG, ZONE, MERGE, VERSION)

_INTENT_ATTR = "_next_partial_intent"
_UNSET = object()


class MergeMode(enum.StrEnum):
    """Merge intent of a paginating partial request."""

    APPEND = "append"
    PREPEND = "prepend"


@dataclass(frozen=True, slots=True)
class PartialIntent:
    """Parsed partial-request headers naming what the client asks for.

    The fields mirror the request-header table of the wire protocol. A
    request without the `X-Next-Request` switch is not partial and every
    derived field stays empty. Names are server-registry indices, never
    selectors or swap strategies.
    """

    partial: bool = False
    zones: tuple[str, ...] = ()
    validate_fields: tuple[str, ...] = ()
    merge: "MergeMode | None" = None
    version: str | None = None
    request_id: str | None = None
    origin: str | None = None

    @property
    def is_partial(self) -> bool:
        """Return True when the request carries the partial switch."""
        return self.partial


def _split_names(raw: str | None) -> tuple[str, ...]:
    """Split a comma-separated header value into trimmed non-empty names."""
    if not raw:
        return ()
    return tuple(name.strip() for name in raw.split(",") if name.strip())


def _parse_merge(raw: str | None) -> "MergeMode | None":
    """Return the merge mode named by the header value, if recognised."""
    if not raw:
        return None
    try:
        return MergeMode(raw.strip())
    except ValueError:
        return None


def _value(request: "HttpRequest", name: str) -> str | None:
    """Return a single request header value, or None when absent."""
    return request.headers.get(name)


def _parse_intent(request: "HttpRequest") -> PartialIntent:
    """Parse the partial-request headers of a request into an intent."""
    if _value(request, REQUEST_FLAG) != "1":
        return PartialIntent()
    return PartialIntent(
        partial=True,
        zones=_split_names(_value(request, ZONE)),
        validate_fields=_split_names(_value(request, VALIDATE)),
        merge=_parse_merge(_value(request, MERGE)),
        version=_value(request, VERSION),
        request_id=_value(request, REQUEST_ID),
        origin=_value(request, ORIGIN),
    )


def partial_intent(request: "HttpRequest") -> PartialIntent:
    """Return the partial intent of the request, memoised on the request."""
    cached = getattr(request, _INTENT_ATTR, _UNSET)
    if cached is not _UNSET:
        return cast("PartialIntent", cached)
    intent = _parse_intent(request)
    setattr(request, _INTENT_ATTR, intent)
    return intent


def is_partial_request(request: "HttpRequest") -> bool:
    """Return True when the request asks for a partial response."""
    return partial_intent(request).partial


def set_partial_vary(response: "HttpResponseBase") -> None:
    """Add the partial Vary headers so shared caches stay unpoisoned."""
    patch_vary_headers(response, VARY_HEADERS)


__all__ = [
    "ACCEPT",
    "CONTENT_TYPE",
    "VARY_HEADERS",
    "MergeMode",
    "PartialIntent",
    "is_partial_request",
    "partial_intent",
    "set_partial_vary",
]
