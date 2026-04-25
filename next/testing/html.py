"""HTML helpers for next-dj tests.

Thin conveniences for the narrow cases that Django's built-in
assertions (`assertContains(html=True)`, `assertInHTML`, ...) do not
cover cleanly: picking a specific anchor out of a rendered page and
checking class-token membership without regex or BeautifulSoup.

These helpers operate on HTML produced by Django template rendering;
they do not try to be a general HTML parser.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser


_ANCHOR_RE = re.compile(r"<a\b[^>]*>[\s\S]*?</a\s*>", re.IGNORECASE)


class _FirstTagAttrs(HTMLParser):
    """Collect attributes of the first start tag encountered."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tag: str | None = None
        self.attrs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record the first start tag's name and attributes."""
        if self.tag is None:
            self.tag = tag
            self.attrs = {k: ("" if v is None else v) for k, v in attrs}

    def error(self, message: str) -> None:  # pragma: no cover
        """Silence the (unused on Py3.10+) abstract error hook."""
        return


class _TextOnly(HTMLParser):
    """Collect text nodes, ignore tags and comments."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        """Append text data chunks."""
        self.parts.append(data)

    def error(self, message: str) -> None:  # pragma: no cover
        """Silence the (unused on Py3.10+) abstract error hook."""
        return


def _first_tag_attrs(fragment: str) -> dict[str, str]:
    parser = _FirstTagAttrs()
    parser.feed(fragment)
    parser.close()
    if parser.tag is None:
        msg = "Fragment does not contain a start tag"
        raise LookupError(msg)
    return parser.attrs


def _inner_text(fragment: str) -> str:
    parser = _TextOnly()
    parser.feed(fragment)
    parser.close()
    return "".join(parser.parts).strip()


def find_anchor(
    html: str,
    *,
    href: str | None = None,
    text: str | None = None,
) -> str:
    """Return the first `<a>...</a>` substring that matches the filters.

    `href` is compared for exact equality with the anchor's `href`
    attribute. `text` is matched as a substring against the anchor's
    stripped inner text. With no filters, returns the first anchor in
    document order. Raises `LookupError` when nothing matches.
    """
    for match in _ANCHOR_RE.finditer(html):
        fragment = match.group(0)
        attrs = _first_tag_attrs(fragment)
        if href is not None and attrs.get("href") != href:
            continue
        if text is not None and text not in _inner_text(fragment):
            continue
        return fragment
    msg = f"Anchor not found: href={href!r} text={text!r}"
    raise LookupError(msg)


def _class_tokens(fragment: str) -> set[str]:
    return set(_first_tag_attrs(fragment).get("class", "").split())


def assert_has_class(fragment: str, token: str) -> None:
    """Raise `AssertionError` unless the tag's class attribute has `token`.

    `token` is matched against whitespace-separated class tokens.
    """
    tokens = _class_tokens(fragment)
    if token not in tokens:
        observed = sorted(tokens) if tokens else "no classes"
        msg = f"Expected class token {token!r}, got {observed}"
        raise AssertionError(msg)


def assert_missing_class(fragment: str, token: str) -> None:
    """Raise `AssertionError` when the tag's class attribute has `token`."""
    tokens = _class_tokens(fragment)
    if token in tokens:
        msg = f"Did not expect class token {token!r}, got {sorted(tokens)}"
        raise AssertionError(msg)


__all__ = ["assert_has_class", "assert_missing_class", "find_anchor"]
