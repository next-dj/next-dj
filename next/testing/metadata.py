"""Read back the head tags `{% metadata %}` rendered, strictly, and assert on them.

A head that renders a single-valued tag twice or a JSON-LD body that is no JSON
raises `HeadParseError`, so the test fails on the markup a crawler would trip on.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Final, override

from .seo import response_text


if TYPE_CHECKING:
    from django.http.response import HttpResponseBase


_LD_TYPE: Final = "application/ld+json"
_GRAPH: Final = "@graph"
_SINGLE_NAMES: Final = frozenset(
    {
        "description",
        "keywords",
        "viewport",
        "robots",
        "googlebot",
        "color-scheme",
        "twitter:card",
        "twitter:site",
        "twitter:site:id",
        "twitter:creator",
        "twitter:creator:id",
        "twitter:title",
        "twitter:description",
        "twitter:player",
        "twitter:player:width",
        "twitter:player:height",
        "twitter:player:stream",
    }
)
"""The meta names a sound head carries at most once."""

_SINGLE_PROPERTIES: Final = frozenset(
    {
        "og:title",
        "og:description",
        "og:url",
        "og:type",
        "og:site_name",
        "og:locale",
        "og:determiner",
    }
)
"""The Open Graph properties a sound head carries at most once."""

_NAMED: Final = ("description", "keywords", "viewport", "robots", "googlebot")
_PREFIXED: Final = {"og": ("property", "og:"), "twitter": ("name", "twitter:")}
_KEYS: Final = frozenset(
    {"title", "canonical", "alternates", "jsonld", *_NAMED, *_PREFIXED}
)


class HeadParseError(ValueError):
    """The head of a page carries a tag twice or a JSON-LD body that is no JSON."""


@dataclass(slots=True)
class HeadTags:
    """The metadata tags of one head, names read lower case.

    `jsonld` lists the members of a `@graph` in place of the graph itself.
    """

    title: str | None = None
    names: dict[str, str] = field(default_factory=dict)
    properties: dict[str, str] = field(default_factory=dict)
    canonical: str | None = None
    alternates: dict[str, str] = field(default_factory=dict)
    jsonld: list[object] = field(default_factory=list)


def _keep(found: dict[str, str], key: str, value: str, *, single: bool) -> None:
    """Record `value` under `key`, the first one kept, a second single one refused."""
    if key not in found:
        found[key] = value
    elif single:
        msg = f"the head carries {key!r} twice, {found[key]!r} and {value!r}"
        raise HeadParseError(msg)


class _HeadParser(HTMLParser):
    """Collect the metadata tags of a document in one pass, up to `</head>`."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags = HeadTags()
        self._text: list[str] | None = None
        self._into: str | None = None
        self._done = False

    def _meta(self, values: dict[str, str | None]) -> None:
        tags = self.tags
        content = values.get("content") or ""
        name = values.get("name")
        if name is not None:
            key = name.lower()
            _keep(tags.names, key, content, single=key in _SINGLE_NAMES)
        prop = values.get("property")
        if prop is not None:
            _keep(tags.properties, prop, content, single=prop in _SINGLE_PROPERTIES)

    def _link(self, values: dict[str, str | None]) -> None:
        tags = self.tags
        rel, href = values.get("rel"), values.get("href") or ""
        if rel == "canonical":
            if tags.canonical is not None:
                msg = (
                    "the head carries two canonical links, "
                    f"{tags.canonical!r} and {href!r}"
                )
                raise HeadParseError(msg)
            tags.canonical = href
        elif rel == "alternate" and "hreflang" in values:
            code = values["hreflang"] or ""
            _keep(tags.alternates, code, href, single=True)

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record a meta or link tag, or start collecting a title or JSON-LD body."""
        if self._done:
            return
        values = dict(attrs)
        if tag == "meta":
            self._meta(values)
        elif tag == "link":
            self._link(values)
        elif tag == "title" or (tag == "script" and values.get("type") == _LD_TYPE):
            self._into = tag
            self._text = []

    @override
    def handle_data(self, data: str) -> None:
        """Collect the body of an open title or JSON-LD script."""
        if self._text is not None:
            self._text.append(data)

    @override
    def handle_endtag(self, tag: str) -> None:
        """Close an open title or JSON-LD script, and stop at the end of the head."""
        if tag == "head":
            self._done = True
        if self._text is None or tag != self._into:
            return
        text = "".join(self._text)
        self._text = self._into = None
        if tag == "title":
            if self.tags.title is not None:
                msg = f"the head carries two titles, {self.tags.title!r} and {text!r}"
                raise HeadParseError(msg)
            self.tags.title = text
            return
        try:
            node = json.loads(text)
        except ValueError as exc:
            msg = f"a JSON-LD script holds no JSON ({exc}): {text!r}"
            raise HeadParseError(msg) from exc
        graph = node.get(_GRAPH) if isinstance(node, dict) else None
        if isinstance(graph, list):
            self.tags.jsonld.extend(graph)
        else:
            self.tags.jsonld.append(node)


def head_tags(html: str) -> HeadTags:
    """Return the metadata tags the head of `html` carries, the whole of a fragment.

    A tag rendered twice that a head carries once, or a JSON-LD body that is no
    JSON, raises `HeadParseError`.
    """
    parser = _HeadParser()
    parser.feed(html)
    parser.close()
    return parser.tags


def _prefixed(tags: HeadTags, block: str, expected: object) -> object:
    """Return the tags of an `og` or `twitter` block, the suffixes `expected` names.

    Without a mapping the whole block answers, `None` when the head has none of it.
    """
    attribute, prefix = _PREFIXED[block]
    found = tags.properties if attribute == "property" else tags.names
    if isinstance(expected, Mapping):
        return {key: found.get(f"{prefix}{key}") for key in expected}
    block_tags = {
        key[len(prefix) :]: value
        for key, value in found.items()
        if key.startswith(prefix)
    }
    return block_tags or None


def _actual(tags: HeadTags, key: str, expected: object) -> object:
    if key in _NAMED:
        return tags.names.get(key)
    if key in _PREFIXED:
        return _prefixed(tags, key, expected)
    if key in {"alternates", "jsonld"} and expected is None:
        return getattr(tags, key) or None
    return getattr(tags, key)


def _wanted(key: str, expected: object) -> object:
    if isinstance(expected, Mapping):
        return dict(expected)
    if key == "jsonld" and isinstance(expected, Sequence):
        return list(expected)
    return expected


def assert_metadata(response: HttpResponseBase | str, **expected: object) -> None:
    """Assert the head tags of a response, `None` expecting a tag to be absent.

    `og` and `twitter` take property suffixes, `alternates` and `jsonld` whole values,
    and `og=None` or `twitter=None` expects no tag of that block at all.
    """
    unknown = sorted(set(expected) - _KEYS)
    if unknown:
        msg = f"assert_metadata() got unknown keys {unknown}, expected {sorted(_KEYS)}"
        raise TypeError(msg)
    html = response if isinstance(response, str) else response_text(response)
    tags = head_tags(html)
    mismatches = [
        f"{key}: expected {want!r}, got {got!r}"
        for key, value in expected.items()
        if (got := _actual(tags, key, value)) != (want := _wanted(key, value))
    ]
    if mismatches:
        raise AssertionError("\n".join(mismatches))


__all__ = ["HeadParseError", "HeadTags", "assert_metadata", "head_tags"]
