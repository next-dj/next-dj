"""HTML helpers for next-dj tests.

Thin conveniences for the narrow cases that Django's built-in
assertions (`assertContains(html=True)`, `assertInHTML`, ...) do not
cover cleanly: picking a specific anchor out of a rendered page and
checking class-token membership without regex or BeautifulSoup.

These helpers operate on HTML produced by Django template rendering.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, override


_ANCHOR_RE = re.compile(r"<a\b[^>]*>[\s\S]*?</a\s*>", re.IGNORECASE)
_FORM_RE = re.compile(r"<form\b[^>]*>[\s\S]*?</form\s*>", re.IGNORECASE)
_INIT_CALL_RE = re.compile(r"Next\._init\(\s*(?=\{)")


class _FirstTagAttrs(HTMLParser):
    """Collect attributes of the first start tag encountered."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tag: str | None = None
        self.attrs: dict[str, str] = {}

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record the first start tag's name and attributes."""
        if self.tag is None:
            self.tag = tag
            self.attrs = {k: ("" if v is None else v) for k, v in attrs}


class _TextOnly(HTMLParser):
    """Collect text nodes, ignore tags and comments."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    @override
    def handle_data(self, data: str) -> None:
        """Append text data chunks."""
        self.parts.append(data)


class _InputFields(HTMLParser):
    """Collect `<input>` name to value pairs, optionally hidden ones only."""

    def __init__(self, *, hidden_only: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_only = hidden_only
        self.fields: dict[str, str] = {}

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record a named input's value, keeping the last duplicate."""
        if tag != "input":
            return
        values = {k: ("" if v is None else v) for k, v in attrs}
        name = values.get("name")
        if name is None or (self.hidden_only and values.get("type") != "hidden"):
            return
        self.fields[name] = values.get("value", "")


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


def _input_fields(fragment: str, *, hidden_only: bool) -> dict[str, str]:
    parser = _InputFields(hidden_only=hidden_only)
    parser.feed(fragment)
    parser.close()
    return parser.fields


def _json_object_at(text: str, start: int) -> str:
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text[start:], start=start):
        if escaped:
            escaped = False
        elif in_string:
            if char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    msg = "Unterminated object in the Next._init call"
    raise LookupError(msg)


def find_anchor(html: str, *, href: str | None = None, text: str | None = None) -> str:
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


def find_form(
    html: str,
    *,
    action: str | None = None,
    contains: str | None = None,
    excludes: str | None = None,
) -> str:
    """Return the first `<form>...</form>` substring that matches the filters.

    `action` is compared for exact equality with the form's `action` attribute,
    `contains` and `excludes` are substrings that must and must not appear in the
    block. With no filters, returns the first form in document order. Raises
    `LookupError` when nothing matches.
    """
    for match in _FORM_RE.finditer(html):
        fragment = match.group(0)
        if action is not None and _first_tag_attrs(fragment).get("action") != action:
            continue
        if contains is not None and contains not in fragment:
            continue
        if excludes is not None and excludes in fragment:
            continue
        return fragment
    msg = (
        f"Form not found: action={action!r} contains={contains!r} excludes={excludes!r}"
    )
    raise LookupError(msg)


def form_action(fragment: str) -> str:
    """Return the `action` attribute of the fragment's first tag.

    Raises `LookupError` when that tag carries no `action`.
    """
    attrs = _first_tag_attrs(fragment)
    action = attrs.get("action")
    if action is None:
        msg = "Fragment's first tag has no action attribute"
        raise LookupError(msg)
    return action


def form_fields(fragment: str) -> dict[str, str]:
    """Return every `<input>` name to value pair inside the fragment.

    Inputs are read in document order, a later duplicate name wins, and an input
    without a `value` attribute maps to the empty string.
    """
    return _input_fields(fragment, hidden_only=False)


def hidden_fields(fragment: str) -> dict[str, str]:
    """Return the hidden `<input>` name to value pairs inside the fragment.

    Same contract as `form_fields`, narrowed to the `type="hidden"` inputs a form
    expects echoed back on submit.
    """
    return _input_fields(fragment, hidden_only=True)


def init_payload(html: str) -> dict[str, Any]:
    """Return the payload of the `Next._init(...)` bootstrap call in a page.

    The call is a script body rather than markup, so the object is located by regex
    and then delimited by a brace scan that survives nesting and braces inside JSON
    strings. Raises `LookupError` when the call is absent or unterminated.
    """
    match = _INIT_CALL_RE.search(html)
    if match is None:
        msg = "Next._init call not found"
        raise LookupError(msg)
    payload: dict[str, Any] = json.loads(_json_object_at(html, match.end()))
    return payload


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


__all__ = [
    "assert_has_class",
    "assert_missing_class",
    "find_anchor",
    "find_form",
    "form_action",
    "form_fields",
    "hidden_fields",
    "init_payload",
]
