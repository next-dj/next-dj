"""The wrapper markup of a deferred boundary and the markers that delimit it."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from django.utils.html import escape

from next.boundaries.failures import DebugBoundaryFailure, ErrorKind
from next.boundaries.ids import ID_HEAD
from next.boundaries.markup import adopt, failed, identity
from next.partial.keys import (
    ADOPTED_ATTR,
    BOUNDARY_KEY_ATTR,
    DETAIL_ATTR,
    DIGEST_ATTR,
    ERROR_ATTR,
    SOURCE_ATTR,
    STATE_ATTR,
)
from next.partial.more import DEFER_ATTR


if TYPE_CHECKING:
    from collections.abc import Iterator

    from next.boundaries.calls import DeferredCall
    from next.boundaries.failures import BoundaryFailure
    from next.boundaries.options import DeferMode


_LOADING: Final = f' {STATE_ATTR}="loading" aria-busy="true"'
_IDLE: Final = f' {STATE_ATTR}="idle"'
_ADOPTED: Final = f" {ADOPTED_ATTR}"
STATE: Final = f' {STATE_ATTR}="'
_OPENED: Final = re.compile(r"([0-9]+):([A-Za-z][A-Za-z0-9-]*)?-->")
_CLOSED: Final = re.compile(r"[0-9]+-->")
_COMMENT: Final = len("<!--")
_SITE: Final = "a deferred boundary"


type _Opening = tuple[int, int, str, str | None]


def _marks(text: str, lead: str, rest: re.Pattern[str]) -> Iterator[re.Match[str]]:
    """Yield what `rest` matches right after each `lead` in `text`, in order.

    The response id is in `lead`, a plain search, so no response compiles a pattern.
    """
    at = text.find(lead)
    while at >= 0:
        found = rest.match(text, at + len(lead))
        if found is None:
            at = text.find(lead, at + 1)
            continue
        yield found
        at = text.find(lead, found.end())


def openings(text: str, response_id: str) -> Iterator[_Opening]:
    """Yield the bounds, id and tag of each opening marker `response_id` minted."""
    lead = f"<!--{ID_HEAD}{response_id}-"
    for found in _marks(text, lead, _OPENED):
        start = found.start() - len(lead)
        yield start, found.end(), text[start + _COMMENT : found.end(1)], found[2]


def _identity(ident: str, call: DeferredCall) -> str:
    source = call.source
    named = "" if source is None else f' {SOURCE_ATTR}="{escape(source)}"'
    return f'{identity(ident)} {BOUNDARY_KEY_ATTR}="{escape(call.key)}"{named}'


def _state(kind: ErrorKind | None, failure: BoundaryFailure | None = None) -> str:
    """Return the state attributes, the digest and the detail shown under `DEBUG`."""
    if kind is None:
        return f' {STATE_ATTR}="ready"'
    state = f' {STATE_ATTR}="error" {ERROR_ATTR}="{kind.value}"'
    if isinstance(failure, DebugBoundaryFailure):
        state += f' {DIGEST_ATTR}="{escape(failure.digest)}"'
        if failure.message:
            state += f' {DETAIL_ATTR}="{escape(failure.message)}"'
    return state


def _element(tag: str | None, attributes: str, body: str) -> str:
    """Return `body` inside a `tag` wrapper, or with the attributes on its one root."""
    if tag is not None:
        return f"<{tag}{attributes}>{body}</{tag}>"
    if not body:
        return f"<template{attributes}></template>"
    return adopt(attributes, body, site=_SITE)


def closed(
    tag: str | None,
    attributes: str,
    body: str,
    kind: ErrorKind | None,
    failure: BoundaryFailure | None,
    retry: str = "",
) -> str:
    """Return the settled wrapper, an error UI marked as an inline boundary is.

    `retry` holds the token attributes an error wrapper offers its retry with.
    """
    if failure is None:
        return _element(tag, attributes + _state(kind), body)
    attributes += retry
    if tag is None and not body:
        return f"<template{attributes}{_state(failure.kind, failure)}></template>"
    return failed(tag, attributes, failure, body, site=_SITE)


def shell(
    ident: str, call: DeferredCall, loading: str, failure: BoundaryFailure | None = None
) -> str:
    """Return the loading wrapper of a queued job, between markers naming its id.

    A `failure` is a loading UI its site refused, which settles the wrapper as an error.
    """
    tag = call.tag
    attributes = _identity(ident, call)
    if failure is None:
        element = _element(tag, attributes + _LOADING, loading)
    else:
        element = closed(tag, attributes, loading, failure.kind, failure)
    return f"<!--{ident}:{tag or ''}-->{element}<!--/{ident}-->"


def in_place(
    ident: str,
    call: DeferredCall,
    html: str,
    failure: BoundaryFailure | None,
    retry: str = "",
) -> str:
    """Return the wrapper of a boundary rendered in place, ready or failed."""
    kind = None if failure is None else failure.kind
    return closed(call.tag, _identity(ident, call), html, kind, failure, retry)


def idle(
    ident: str,
    call: DeferredCall,
    loading: str,
    *,
    mode: DeferMode,
    token: str,
    failure: BoundaryFailure | None = None,
) -> str:
    """Return the wrapper of a client-trip boundary, its token and its loading UI.

    A wrapper adopting the root of its loading UI says so, as the browser swaps it.
    """
    tag = call.tag
    attributes = f'{_identity(ident, call)} {DEFER_ATTR}="{mode.value}"{token}'
    if failure is not None:
        return closed(tag, attributes, loading, failure.kind, failure)
    if tag is None:
        attributes += _ADOPTED
    return _element(tag, attributes + _IDLE, loading)


def wrappers(html: str, response_id: str) -> dict[str, tuple[str | None, str]]:
    """Return the tag and the identity attributes of each loading wrapper in `html`.

    It reads the markers of the response `response_id` names, so it runs before `strip`.
    """
    found: dict[str, tuple[str | None, str]] = {}
    for _, opened, ident, tag in openings(html, response_id):
        start = html.find(f' id="{ident}"', opened)
        end = -1 if start < 0 else html.find(STATE, start)
        if end >= 0:
            found[ident] = (tag or None, html[start:end])
    return found


def strip(shell_html: str, response_id: str) -> str:
    """Return the shell without the markers of the response `response_id` names."""
    closer = f"<!--/{ID_HEAD}{response_id}-"
    spans = [(start, end) for start, end, _, _ in openings(shell_html, response_id)]
    spans += [
        (found.start() - len(closer), found.end())
        for found in _marks(shell_html, closer, _CLOSED)
    ]
    if not spans:
        return shell_html
    spans.sort()
    parts: list[str] = []
    position = 0
    for start, end in spans:
        parts.append(shell_html[position:start])
        position = end
    parts.append(shell_html[position:])
    return "".join(parts)


__all__ = [
    "STATE",
    "closed",
    "idle",
    "in_place",
    "openings",
    "shell",
    "strip",
    "wrappers",
]
