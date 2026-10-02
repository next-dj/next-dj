"""Read a sitemap response back into the URLs a test asserts on."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final


if TYPE_CHECKING:
    from django.http.response import HttpResponseBase


_XHTML_LINK: Final = "{http://www.w3.org/1999/xhtml}link"
_ENTRIES: Final = frozenset({"url", "sitemap"})


@dataclass(frozen=True, slots=True)
class SitemapUrl:
    """One `<url>` or index `<sitemap>` entry, its alternates as hreflang pairs."""

    loc: str
    lastmod: str | None = None
    alternates: tuple[tuple[str, str], ...] = ()


def _local(tag: str) -> str:
    """Return a tag without its namespace, so a bare test fixture reads the same."""
    return tag.rpartition("}")[2]


def _text(entry: ET.Element, name: str) -> str | None:
    found = next((child for child in entry if _local(child.tag) == name), None)
    return None if found is None else (found.text or "").strip()


def _entry(entry: ET.Element) -> SitemapUrl:
    alternates = tuple(
        (code, href)
        for link in entry.iter(_XHTML_LINK)
        if link.get("rel") == "alternate"
        and (code := link.get("hreflang"))
        and (href := link.get("href"))
    )
    return SitemapUrl(
        loc=_text(entry, "loc") or "",
        lastmod=_text(entry, "lastmod"),
        alternates=alternates,
    )


def _response_text(response: HttpResponseBase) -> str:
    """Return the body of a response as text, a streamed one read to its end."""
    if getattr(response, "streaming", False):
        body = b"".join(getattr(response, "streaming_content", ()))
    else:
        body = getattr(response, "content", b"")
    return body.decode(response.charset or "utf-8", errors="replace")


def parse_sitemap(response: HttpResponseBase) -> list[SitemapUrl]:
    """Return the URLs a sitemap or a sitemap index response lists, in order.

    A body that is no well-formed XML raises `ET.ParseError`, failing the test.
    """
    # A test reads the response of its own site, so the stdlib parser is fine here.
    root = ET.fromstring(_response_text(response))  # noqa: S314
    return [_entry(entry) for entry in root if _local(entry.tag) in _ENTRIES]


__all__ = ["SitemapUrl", "parse_sitemap"]
