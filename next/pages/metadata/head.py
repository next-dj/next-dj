"""The head tags a rendered page carries, read back from its HTML in one pass."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Final, override


_LD_TYPE: Final = "application/ld+json"
_GRAPH: Final = "@graph"


@dataclass(slots=True)
class HeadTags:
    """The head tags of one page, the first value of each meta name kept.

    `robots` lists every robots meta, and `jsonld` the members of a `@graph` too.
    """

    title: str | None = None
    names: dict[str, str] = field(default_factory=dict)
    properties: dict[str, str] = field(default_factory=dict)
    canonical: str | None = None
    alternates: dict[str, str] = field(default_factory=dict)
    jsonld: list[object] = field(default_factory=list)
    robots: list[str] = field(default_factory=list)


class _HeadParser(HTMLParser):
    """Collect the metadata tags of a document in one pass."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags = HeadTags()
        self._text: list[str] | None = None
        self._into: str | None = None

    def _meta(self, values: dict[str, str | None]) -> None:
        tags = self.tags
        content = values.get("content")
        name = values.get("name")
        if name is not None:
            tags.names.setdefault(name.lower(), content or "")
            if name.lower() == "robots" and content is not None:
                tags.robots.append(content)
        prop = values.get("property")
        if prop is not None:
            tags.properties.setdefault(prop, content or "")

    def _link(self, values: dict[str, str | None]) -> None:
        tags = self.tags
        rel, href = values.get("rel"), values.get("href") or ""
        if rel == "canonical" and tags.canonical is None:
            tags.canonical = href
        elif rel == "alternate" and "hreflang" in values:
            tags.alternates[values["hreflang"] or ""] = href

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record a meta or link tag, or start collecting a title or JSON-LD body."""
        values = dict(attrs)
        if tag == "meta":
            self._meta(values)
        elif tag == "link":
            self._link(values)
        elif (tag == "title" and self.tags.title is None) or (
            tag == "script" and values.get("type") == _LD_TYPE
        ):
            self._into = tag
            self._text = []

    @override
    def handle_data(self, data: str) -> None:
        """Collect the body of an open title or JSON-LD script."""
        if self._text is not None:
            self._text.append(data)

    @override
    def handle_endtag(self, tag: str) -> None:
        """Close an open title or JSON-LD script, keeping a body that is no JSON."""
        if self._text is None or tag != self._into:
            return
        text = "".join(self._text)
        self._text = self._into = None
        if tag == "title":
            self.tags.title = text
            return
        try:
            node = json.loads(text)
        except ValueError:
            self.tags.jsonld.append(text)
            return
        graph = node.get(_GRAPH) if isinstance(node, dict) else None
        if isinstance(graph, list):
            self.tags.jsonld.extend(graph)
        else:
            self.tags.jsonld.append(node)


def head_tags(html: str) -> HeadTags:
    """Return the metadata tags `html` carries."""
    parser = _HeadParser()
    parser.feed(html)
    parser.close()
    return parser.tags


__all__ = ["HeadTags", "head_tags"]
