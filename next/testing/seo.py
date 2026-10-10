"""Read a sitemap response back into the URLs a test asserts on."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from defusedxml.ElementTree import fromstring

from .responses import response_text


if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

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
    """Return a tag without its namespace, so a fixture without one parses alike."""
    return tag.rpartition("}")[2]


def _text(entry: Element, name: str) -> str | None:
    found = next((child for child in entry if _local(child.tag) == name), None)
    return None if found is None else (found.text or "").strip()


def _entry(entry: Element) -> SitemapUrl:
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


def parse_sitemap(response: HttpResponseBase) -> list[SitemapUrl]:
    """Return the URLs a sitemap or a sitemap index response lists, in order.

    A body that is no well-formed XML raises `ParseError`, failing the test.
    """
    root = fromstring(response_text(response))
    return [_entry(entry) for entry in root if _local(entry.tag) in _ENTRIES]


__all__ = ["SitemapUrl", "parse_sitemap"]
