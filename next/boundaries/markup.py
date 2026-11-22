"""The wrapper markup of a failed boundary and where a wrapper element may sit."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from django.conf import settings
from django.utils.html import escape

from next.defer import DEFAULT_TAG, BoundaryFailure, DebugBoundaryFailure, ErrorKind

from .elements import missed, open_elements, roots
from .failures import Trace
from .options import error_settings
from .reporting import failure_log


if TYPE_CHECKING:
    from next.static.collector import StaticCollector


_RETRY_BUNDLE: Final = "defer"

RETRY_ATTR: Final = "data-next-retry"
"""The attribute of a retry control in an error UI, which the `defer` bundle answers."""

_RETRY_CONTROL: Final = re.compile(
    r"<[a-z][^\s/>]*"
    r"(?:\s+[^\s\"'=<>/]+(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'>]+))?)*?"
    rf"\s+{RETRY_ATTR}(?=[\s=/>])",
    re.IGNORECASE,
)

ADOPTING_ELEMENTS: Final = frozenset(
    {
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "ul",
        "ol",
        "dl",
        "select",
        "optgroup",
        "colgroup",
    }
)
"""The elements no wrapper element may sit directly inside, `svg` holding any depth."""

_FOREIGN: Final = "svg"
_HTML_INSIDE_FOREIGN: Final = "foreignobject"

_DETAIL_HOSTS: Final = frozenset(
    {
        "article",
        "aside",
        "blockquote",
        "dd",
        "dialog",
        "div",
        "fieldset",
        "figure",
        "footer",
        "header",
        "li",
        "main",
        "nav",
        "section",
        "td",
        "th",
    }
)
"""The wrappers a `<details>` child stays inside, where the HTML parser moves none."""

_NOT_ONE_ROOT: Final = (
    "The error UI of %s renders %s where its boundary adopts the root element, so "
    "an empty template element carries the boundary instead."
)


def wrapper_tag(preceding: str, tag: str = DEFAULT_TAG) -> str | None:
    """Return `tag`, or `None` when the markup before a site leaves no valid wrapper.

    Inside a table, a list, a select or an `svg` the boundary adopts its root instead.
    """
    stack = open_elements(preceding)
    for name, _attrs in reversed(stack):
        if name == _HTML_INSIDE_FOREIGN:
            break
        if name == _FOREIGN:
            return None
    if stack and stack[-1][0] in ADOPTING_ELEMENTS:
        return None
    return tag


def debug_detail(trace: Trace) -> str:
    """Return the unstyled element that shows the exception line and its traceback."""
    return (
        f"<details open data-next-error-detail><summary>{escape(trace.line)}</summary>"
        f"<pre>{escape(trace.text)}</pre></details>"
    )


def _detail_attribute(text: str) -> str:
    return f' data-next-error-detail="{escape(text)}"'


@dataclass(frozen=True, slots=True)
class Shown:
    """What the wrapper of one failure shows beside the error UI."""

    trace: Trace | None
    site: str
    debug: bool


_PLAIN: Final = Shown(None, "a boundary", debug=False)


def _adopted(attributes: str, body: str, shown: Shown) -> str:
    """Return `body` with `attributes` on its one root, else an empty placeholder.

    The detail is an attribute, since no element is valid beside a table row.
    """
    detail = "" if shown.trace is None else shown.trace.text
    ends, loose = roots(body)
    found = missed(ends, loose=loose)
    if found is None:
        end = ends[0]
        extra = _detail_attribute(detail) if detail else ""
        return f"{body[:end]}{attributes}{extra}{body[end:]}"
    failure_log.warn(("roots", shown.site), _NOT_ONE_ROOT, shown.site, found)
    if shown.debug:
        message = _NOT_ONE_ROOT % (shown.site, found)
        detail = f"{message}\n\n{detail}" if detail else message
    extra = _detail_attribute(detail) if detail else ""
    return f"<template{attributes}{extra}></template>"


def wrap(tag: str | None, attributes: str, body: str, shown: Shown) -> str:
    """Return `body` inside a `tag` wrapper, else adopted by its one root element."""
    if tag is None:
        return _adopted(attributes, body, shown)
    trace = shown.trace
    if trace is not None:
        if tag in _DETAIL_HOSTS:
            body += debug_detail(trace)
        else:
            attributes += _detail_attribute(trace.text)
    return f"<{tag}{attributes}>{body}</{tag}>"


def identity(ident: str) -> str:
    """Return the `id` and `data-next-boundary` attributes a wrapper is swapped by."""
    shown = escape(ident)
    return f' id="{shown}" data-next-boundary="{shown}"'


def _failure(kind: ErrorKind, digest: str | None) -> str:
    shown = "" if digest is None else f' data-next-error-digest="{escape(digest)}"'
    return f' data-next-state="error" data-next-error="{escape(kind.value)}"{shown}'


def error_attributes(
    ident: str, kind: ErrorKind, digest: str | None, source: str | None = None
) -> str:
    """Return the attributes of a wrapper that settled as an error."""
    named = "" if source is None else f' data-next-boundary-source="{escape(source)}"'
    return identity(ident) + _failure(kind, digest) + named


def adopt(attributes: str, body: str, *, site: str) -> str:
    """Return `body` with `attributes` on its one root, else an empty placeholder.

    `site` names the boundary in the warning a body without one root logs.
    """
    return _adopted(attributes, body, Shown(None, site, debug=bool(settings.DEBUG)))


def failed(
    tag: str | None, attributes: str, error: BoundaryFailure, body: str, *, site: str
) -> str:
    """Return the error UI `body` in a failed wrapper, marked as an inline one is.

    The digest and the traceback detail show for a `DebugBoundaryFailure` alone.
    """
    trace = None
    digest = None
    if isinstance(error, DebugBoundaryFailure):
        digest = error.digest
        if error.traceback and error_settings().detail:
            trace = Trace(error.message, error.traceback)
    marked = attributes + _failure(error.kind, digest)
    return wrap(tag, marked, body, Shown(trace, site, debug=digest is not None))


def wrapped(
    tag: str | None,
    ident: str,
    kind: ErrorKind,
    body: str,
    digest: str | None = None,
    source: str | None = None,
) -> str:
    """Return `body` inside the wrapper of a boundary that settled as an error.

    A `None` tag stamps the wrapper attributes on the single root element of `body`.
    """
    return wrap(tag, error_attributes(ident, kind, digest, source), body, _PLAIN)


def note_retry(collector: StaticCollector | None, body: str, token: str) -> None:
    """Record the `defer` bundle an error UI offering a retry needs.

    A retry token on its wrapper or a start tag carrying the retry attribute offers one.
    """
    if collector is not None and (token or _offers_retry(body)):
        collector.require(_RETRY_BUNDLE)


def _offers_retry(body: str) -> bool:
    """Whether a start tag of `body` carries the retry attribute, text naming it not."""
    return _RETRY_CONTROL.search(body) is not None


__all__ = [
    "ADOPTING_ELEMENTS",
    "RETRY_ATTR",
    "Shown",
    "adopt",
    "debug_detail",
    "error_attributes",
    "failed",
    "identity",
    "note_retry",
    "wrap",
    "wrapped",
    "wrapper_tag",
]
