"""Partial-request headers, intent parsing, and Vary stamping."""

import enum
import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from django.http import HttpResponse
from django.utils.cache import patch_vary_headers

from next.conf.sentinels import UNSET
from next.defer import Depth


if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponseBase


REQUEST_FLAG = "X-Next-Request"
ZONE = "X-Next-Zone"
VALIDATE = "X-Next-Validate"
MERGE = "X-Next-Merge"
VERSION = "X-Next-Version"
REQUEST_ID = "X-Next-Request-Id"
ORIGIN = "X-Next-Origin"

# The response echoes the version under the same name the request asserts it.
RESPONSE_VERSION = VERSION
RESPONSE_FORM = "X-Next-Form"
RESPONSE_ACTION = "X-Next-Action"

CONTENT_TYPE = "application/vnd.next.patches+json"
ACCEPT = "application/vnd.next.patches+json, text/html;q=0.9"
STREAM_ACCEPT = "text/event-stream, application/json;q=0.9"
EVENT_STREAM = "text/event-stream"

BOUNDARY = "X-Next-Boundary"
SEC_FETCH_SITE = "Sec-Fetch-Site"

NAVIGATE = "X-Next-Navigate"
LAYOUTS = "X-Next-Layouts"
PREFETCH = "X-Next-Prefetch"
ROUTER = "X-Next-Router"

# VALIDATE and REQUEST_ID never change the cached body, so not in Vary.
VARY_HEADERS: tuple[str, ...] = (
    REQUEST_FLAG,
    ZONE,
    MERGE,
    VERSION,
    BOUNDARY,
    NAVIGATE,
    LAYOUTS,
    PREFETCH,
)
STREAM_VARY: tuple[str, ...] = ("Accept",)

_INTENT_ATTR = "_next_partial_intent"
_JSON = "application/json"
_RANKED = frozenset({EVENT_STREAM, _JSON})
_WIRE_DEPTHS: Final[dict[str, Depth]] = {"1": Depth.SHELL, "full": Depth.FULL}


class MergeMode(enum.StrEnum):
    """Merge intent of a paginating partial request."""

    APPEND = "append"
    PREPEND = "prepend"


@dataclass(frozen=True, slots=True)
class PartialIntent:
    """Parsed partial-request headers naming what the client asks for.

    Without the `X-Next-Request` switch every derived field stays empty. `layouts` is
    the raw declaration of a navigation, `prefetch` the depth a router prefetch wants.
    """

    partial: bool = False
    zones: tuple[str, ...] = ()
    validate_fields: tuple[str, ...] = ()
    merge: "MergeMode | None" = None
    version: str | None = None
    request_id: str | None = None
    origin: str | None = None
    navigate: bool = False
    layouts: str | None = None
    prefetch: Depth | None = None


# Every field is immutable and empty, so non-partial requests share one instance.
_NOT_PARTIAL = PartialIntent()


def _split_names(raw: str | None) -> tuple[str, ...]:
    """Split a comma-separated header value into trimmed non-empty names."""
    if not raw:
        return ()
    return tuple(stripped for name in raw.split(",") if (stripped := name.strip()))


def _parse_merge(raw: str | None) -> "MergeMode | None":
    """Return the merge mode named by the header value, if recognised."""
    if not raw:
        return None
    try:
        return MergeMode(raw.strip())
    except ValueError:
        return None


@functools.cache
def _meta_key(name: str) -> str:
    """Return the WSGI `META` key of a header name."""
    return "HTTP_" + name.upper().replace("-", "_")


def _value(request: "HttpRequest", name: str) -> str | None:
    """Return a request header value from `META`, skipping the parsed headers map."""
    return request.META.get(_meta_key(name))


def _parse_intent(request: "HttpRequest") -> PartialIntent:
    """Parse the partial-request headers of a request into an intent."""
    if _value(request, REQUEST_FLAG) != "1":
        return _NOT_PARTIAL
    navigate = _value(request, NAVIGATE) == "1"
    wire = _value(request, PREFETCH) if navigate else None
    return PartialIntent(
        partial=True,
        zones=_split_names(_value(request, ZONE)),
        validate_fields=_split_names(_value(request, VALIDATE)),
        merge=_parse_merge(_value(request, MERGE)),
        version=_value(request, VERSION),
        request_id=_value(request, REQUEST_ID),
        origin=_value(request, ORIGIN),
        navigate=navigate,
        layouts=_value(request, LAYOUTS) if navigate else None,
        prefetch=_WIRE_DEPTHS.get(wire) if wire is not None else None,
    )


def partial_intent(request: "HttpRequest") -> PartialIntent:
    """Return the partial intent of the request, memoised on the request."""
    cached = getattr(request, _INTENT_ATTR, UNSET)
    if cached is not UNSET:
        return cast("PartialIntent", cached)
    intent = _parse_intent(request)
    setattr(request, _INTENT_ATTR, intent)
    return intent


def is_partial_request(request: "HttpRequest") -> bool:
    """Return True when the request asks for a partial response."""
    return partial_intent(request).partial


@functools.lru_cache(maxsize=256)
def _varied(held: str | None, names: tuple[str, ...]) -> str:
    """Return the `Vary` value `patch_vary_headers` leaves a response holding `held`."""
    carrier = HttpResponse()
    if held is not None:
        carrier.headers["Vary"] = held
    patch_vary_headers(carrier, names)
    return carrier.headers["Vary"]


def vary_on(response: "HttpResponseBase", names: tuple[str, ...]) -> None:
    """Add `names` to `Vary` as `patch_vary_headers` does, each merge computed once."""
    headers = response.headers
    held = headers.get("Vary")
    merged = _varied(held, names)
    if merged != held:
        headers["Vary"] = merged


def set_partial_vary(response: "HttpResponseBase") -> None:
    """Add the partial Vary headers so shared caches stay unpoisoned."""
    vary_on(response, VARY_HEADERS)


def set_stream_vary(response: "HttpResponseBase") -> None:
    """Add `Accept` to Vary, as a partial `GET` answers a stream or JSON by it."""
    vary_on(response, STREAM_VARY)


def _quality(params: list[str]) -> float:
    """Return the `q` parameter of one media range, `1` when absent, `0` unreadable."""
    for param in params:
        name, _, value = param.partition("=")
        if name.strip().lower() == "q":
            try:
                quality = float(value.strip())
            except ValueError:
                return 0.0
            return quality if 0 <= quality <= 1 else 0.0
    return 1.0


def accepts_event_stream(request: "HttpRequest") -> bool:
    """Whether `Accept` ranks `text/event-stream` above zero and no lower than JSON.

    A wildcard range accepts nothing here, as only a client with a stream reader asks.
    """
    accept = request.META.get("HTTP_ACCEPT", "").lower()
    if EVENT_STREAM not in accept:
        return False
    qualities: dict[str, float] = {}
    for media_range in accept.split(","):
        media, *params = media_range.split(";")
        name = media.strip()
        if name in _RANKED and name not in qualities:
            qualities[name] = _quality(params)
    stream = qualities.get(EVENT_STREAM, 0.0)
    return stream > 0 and stream >= qualities.get(_JSON, 0.0)


__all__ = [
    "ACCEPT",
    "BOUNDARY",
    "CONTENT_TYPE",
    "EVENT_STREAM",
    "LAYOUTS",
    "NAVIGATE",
    "PREFETCH",
    "ROUTER",
    "SEC_FETCH_SITE",
    "STREAM_ACCEPT",
    "STREAM_VARY",
    "VARY_HEADERS",
    "MergeMode",
    "PartialIntent",
    "accepts_event_stream",
    "is_partial_request",
    "partial_intent",
    "set_partial_vary",
    "set_stream_vary",
    "vary_on",
]
