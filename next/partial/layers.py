"""The loading UI a page carries for each layer zone its links open.

A layer shows it before its answer arrives, so it rides the page that holds the link.
"""

import re
from contextvars import ContextVar
from html import unescape
from typing import TYPE_CHECKING, Final
from urllib.parse import urljoin, urlsplit

from django.template import Context
from django.urls import get_script_prefix

from next.boundaries.stash import stash_markup
from next.caches import BoundedCache
from next.forms.origin import resolve_url_to_page
from next.pages.loaders import module_version
from next.pages.paths import page_template_path
from next.pages.skeletons import render_skeleton, zone_skeleton
from next.seeding import COLLECTOR_KEY
from next.static.runtime import RELOAD_CHUNK_KEY

from .intercept import pick_intercept


if TYPE_CHECKING:
    from pathlib import Path

    from django.http import HttpRequest

    from next.static import StaticCollector


LAYER_ATTR: Final = "data-next-layer"
"""The attribute naming the zone a link opens and the `<template>` of its loading UI."""

_PART: Final = r"""(?:[^\s"'>]++|\s|"[^"]*+"|'[^']*+')"""
_SCAN: Final = re.compile(
    r"<!--.*?-->"
    rf"|<(script|style|textarea|title|template)\b{_PART}*+>.*?</\1\s*+>"
    rf"|<[a-z][^\s/>]*+(?={_PART}*?\s{LAYER_ATTR}\s*+=){_PART}*+>",
    re.IGNORECASE | re.DOTALL,
)
"""A comment, an inert element whole, or a start tag naming a layer zone."""

layer_links: Final[ContextVar[bool]] = ContextVar("next_layer_links", default=True)
"""Whether the page rendering may hold a layer link, which its plan decides."""

_ATTR: Final = re.compile(
    r"""\s([^\s"'>/=]++)\s*+=\s*+(?:"([^"]*+)"|'([^']*+)'|([^\s"'>]++))"""
)
_SERVED: BoundedCache[tuple[str, str, object, str], tuple[int, "Path | None"]] = (
    BoundedCache(depends=())
)


def _attrs(tag: str) -> dict[str, str]:
    """Return the valued attributes of a start tag, the first of a repeated name."""
    found: dict[str, str] = {}
    for match in _ATTR.finditer(tag):
        name = match[1].lower()
        if name not in found:
            found[name] = unescape(match[2] or match[3] or match[4] or "")
    return found


def _served(path: str, zone: str, request: "HttpRequest") -> "Path | None":
    """Return the page serving `zone` at `path`, an intercept over it included.

    The URLconf and the script prefix of the request decide what `path` resolves to.
    """
    version = module_version()
    key = (path, zone, getattr(request, "urlconf", None), get_script_prefix())
    held = _SERVED.get(key)
    if held is not None and held[0] == version:
        return held[1]
    page = resolve_url_to_page(path, request)
    if page is not None:
        rule = pick_intercept(page, (zone,))
        page = page if rule is None else rule.file
    _SERVED[key] = (version, page)
    return page


def _skeleton(href: str, zone: str, request: "HttpRequest") -> "Path | None":
    """Return the `loading.<zone>.djx` of the page a layer link to `href` opens."""
    parts = urlsplit(href)
    if parts.netloc and parts.netloc != request.get_host():
        return None
    page = _served(urljoin(request.path, parts.path), zone, request)
    return None if page is None else zone_skeleton(page_template_path(page), zone)


def stash_layers(
    html: str,
    collector: "StaticCollector",
    request: "HttpRequest | None",
    shown: set[str] | None = None,
) -> str:
    """Return `html` with the loading UI of each layer zone before its first link.

    The first link of a zone names its page, and `shown` holds the zones seen before.
    """
    if request is None or not layer_links.get() or LAYER_ATTR not in html:
        return html
    stashed = set() if shown is None else shown
    parts: list[str] = []
    last = 0
    for found in _SCAN.finditer(html):
        tag = found[0]
        if found[1] is not None or tag.startswith("<!--"):
            continue
        attrs = _attrs(tag)
        zone = attrs.get(LAYER_ATTR, "")
        href = attrs.get("href", "")
        if not zone or not href or zone in stashed:
            continue
        stashed.add(zone)
        source = _skeleton(href, zone, request)
        if source is None:
            continue
        context = Context({"request": request, COLLECTOR_KEY: collector})
        loading = render_skeleton(source, context)
        parts += (html[last : found.start()], stash_markup(zone, loading, LAYER_ATTR))
        last = found.start()
    if not parts:
        return html
    collector.require(RELOAD_CHUNK_KEY)
    parts.append(html[last:])
    return "".join(parts)


__all__ = ["LAYER_ATTR", "layer_links", "stash_layers"]
