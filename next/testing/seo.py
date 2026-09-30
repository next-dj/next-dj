"""Read a sitemap response back into the URLs a test asserts on."""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import TYPE_CHECKING, override


if TYPE_CHECKING:
    from django.http.response import HttpResponseBase


@dataclass(frozen=True, slots=True)
class SitemapUrl:
    """One `<url>` or index `<sitemap>` entry, its alternates as hreflang pairs."""

    loc: str
    lastmod: str | None = None
    alternates: tuple[tuple[str, str], ...] = ()


@dataclass(slots=True)
class _Open:
    loc: list[str] = field(default_factory=list)
    lastmod: list[str] | None = None
    alternates: list[tuple[str, str]] = field(default_factory=list)


class _SitemapReader(HTMLParser):
    """Collect the entries of a sitemap or an index, entities already decoded."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[SitemapUrl] = []
        self._open: _Open | None = None
        self._text: list[str] | None = None

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        opened = self._open
        if tag in {"url", "sitemap"}:
            self._open = _Open()
        elif opened is not None and tag == "loc":
            self._text = opened.loc
        elif opened is not None and tag == "lastmod":
            opened.lastmod = self._text = []
        elif opened is not None and tag == "xhtml:link":
            values = dict(attrs)
            code, href = values.get("hreflang"), values.get("href")
            if values.get("rel") == "alternate" and code and href:
                opened.alternates.append((code, href))

    @override
    def handle_data(self, data: str) -> None:
        if self._text is not None:
            self._text.append(data)

    @override
    def handle_endtag(self, tag: str) -> None:
        opened = self._open
        if tag in {"loc", "lastmod"}:
            self._text = None
        elif tag in {"url", "sitemap"} and opened is not None:
            lastmod = None if opened.lastmod is None else "".join(opened.lastmod)
            self.urls.append(
                SitemapUrl(
                    loc="".join(opened.loc).strip(),
                    lastmod=None if lastmod is None else lastmod.strip(),
                    alternates=tuple(opened.alternates),
                )
            )
            self._open = None


def _response_text(response: HttpResponseBase) -> str:
    """Return the body of a response as text, a streamed one read to its end."""
    if getattr(response, "streaming", False):
        body = b"".join(getattr(response, "streaming_content", ()))
    else:
        body = getattr(response, "content", b"")
    return body.decode(response.charset or "utf-8", errors="replace")


def parse_sitemap(response: HttpResponseBase) -> list[SitemapUrl]:
    """Return the URLs a sitemap or a sitemap index response lists, in order."""
    reader = _SitemapReader()
    reader.feed(_response_text(response))
    reader.close()
    return reader.urls


__all__ = ["SitemapUrl", "parse_sitemap"]
