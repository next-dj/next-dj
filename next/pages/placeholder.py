"""The layout placeholder and root marker grammar, and the scans that locate them.

Composition and the layout checks share these spellings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from django.template.base import DebugLexer, TokenType


if TYPE_CHECKING:
    from collections.abc import Iterator


# The canonical spellings, written by composition and named by the checks.
PLACEHOLDER = "{% template %}"
PLACEHOLDER_OPEN = "{% #template %}"
PLACEHOLDER_CLOSE = "{% /template %}"
ROOT_MARKER = "{% layout root %}"

_SINGLE_TAG = "template"
_OPEN_TAG = "#template"
_CLOSE_TAG = "/template"
_COMMENT_TAG = "comment"
_END_COMMENT_TAG = "endcomment"
_LOAD_TAG = "load"
_ROOT_MARKER_WORDS = ["layout", "root"]
_HTML_OPEN = re.compile(r"<html(?=[\s>/])", re.IGNORECASE)


def _tokens(content: str) -> Iterator[tuple[TokenType, str, int, int]]:
    """Yield the type, the contents and the bounds of every token Django lexes."""
    for token in DebugLexer(content).tokenize():
        # The debug lexer records token bounds that the plain lexer leaves unset.
        start, end = cast("tuple[int, int]", token.position)
        yield token.token_type, token.contents, start, end


def _block_tags(content: str) -> Iterator[tuple[str, int, int]]:
    """Yield the command and the bounds of every block tag Django lexes in `content`.

    A tag written under `{% verbatim %}` is text to the lexer, so it never comes out.
    """
    for token_type, contents, start, end in _tokens(content):
        if token_type is TokenType.BLOCK:
            head = contents.split(maxsplit=1)
            yield (head[0] if head else ""), start, end


def placeholder_spans(content: str) -> list[tuple[int, int]]:
    """Return the bounds of every placeholder a parse of `content` would reach.

    Django's lexer decides what is a tag, so a `{% verbatim %}` placeholder is text.
    """
    spans: list[tuple[int, int]] = []
    open_at: int | None = None
    commented = False
    for command, start, end in _block_tags(content):
        if commented:
            commented = command != _END_COMMENT_TAG
        elif command == _COMMENT_TAG:
            commented = True
        elif open_at is not None:
            # The fallback runs to the close, and a placeholder in it renders.
            if command == _CLOSE_TAG:
                spans.append((open_at, end))
                open_at = None
        elif command == _OPEN_TAG:
            open_at = start
        elif command == _SINGLE_TAG:
            spans.append((start, end))
    return spans


@dataclass(frozen=True, slots=True)
class RootScan:
    """Where the root markers of a layout stand and whether the layout is a root."""

    is_root: bool
    markers: tuple[tuple[int, int], ...]


def scan_root(content: str) -> RootScan:
    """Locate the root markers of `content` and decide whether it is a root layout.

    Only `load` tags, comments and blank text may precede the one marker.
    """
    if _ROOT_MARKER_WORDS[0] not in content:
        return RootScan(is_root=False, markers=())
    markers: list[tuple[int, int]] = []
    clean_prefix = True
    first_in_place = False
    commented = False
    for token_type, contents, start, end in _tokens(content):
        words = contents.split()
        command = words[0] if words else ""
        is_block = token_type is TokenType.BLOCK
        if commented:
            commented = not (is_block and command == _END_COMMENT_TAG)
        elif token_type is TokenType.TEXT:
            clean_prefix = clean_prefix and not contents.strip()
        elif is_block and command == _COMMENT_TAG:
            commented = True
        elif is_block and words == _ROOT_MARKER_WORDS:
            if not markers:
                first_in_place = clean_prefix
            markers.append((start, end))
            clean_prefix = False
        elif token_type is TokenType.COMMENT or (is_block and command == _LOAD_TAG):
            pass
        else:
            clean_prefix = False
    return RootScan(first_in_place and len(markers) == 1, tuple(markers))


def opens_html_document(content: str) -> bool:
    """Whether `content` writes an `<html` tag in text outside a `{% comment %}`."""
    commented = False
    for token_type, contents, _start, _end in _tokens(content):
        if token_type is TokenType.BLOCK:
            words = contents.split()
            command = words[0] if words else ""
            if commented:
                commented = command != _END_COMMENT_TAG
            elif command == _COMMENT_TAG:
                commented = True
        elif (
            token_type is TokenType.TEXT
            and not commented
            and _HTML_OPEN.search(contents)
        ):
            return True
    return False


__all__ = [
    "PLACEHOLDER",
    "PLACEHOLDER_CLOSE",
    "PLACEHOLDER_OPEN",
    "ROOT_MARKER",
    "RootScan",
    "opens_html_document",
    "placeholder_spans",
    "scan_root",
]
