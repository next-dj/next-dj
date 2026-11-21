"""The layout placeholder and root marker grammar, and the scans that locate them.

Composition and the layout checks share these spellings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast
from urllib.parse import quote, unquote

from django.template.base import DebugLexer, TokenType


if TYPE_CHECKING:
    from collections.abc import Iterator


# The canonical spellings, written by composition and named by the checks.
PLACEHOLDER = "{% template %}"
PLACEHOLDER_OPEN = "{% #template %}"
PLACEHOLDER_CLOSE = "{% /template %}"
ROOT_MARKER = "{% layout root %}"
BOUNDARY_TAG = "next:boundary"
BOUNDARY_END_TAG = "/next:boundary"
FAILED_TAG = "next:failed"
SLOT_TAG = "next:slot"
SLOT_END_TAG = "/next:slot"
NO_WRAPPER: Final = "-"
"""The wrapper argument of the boundary tag for a slot that takes no wrapper element."""

_SINGLE_TAG = "template"
_OPEN_TAG = "#template"
_CLOSE_TAG = "/template"
_COMMENT_TAG = "comment"
_END_COMMENT_TAG = "endcomment"
_VERBATIM_TAG = "verbatim"
_LOAD_TAG = "load"
_ROOT_MARKER_WORDS = ["layout", "root"]
# A tag cut short by `{{` or `{%` still opens the document, `<htmlx>` does not.
_HTML_OPEN = re.compile(r"<html(?![\w:-])", re.IGNORECASE)
_HTML_COMMENT_OPEN = "<!--"
_HTML_COMMENT_CLOSE = "-->"
_PLACEHOLDER_TEXT = re.compile(r"\{%\s*#?template\s*%\}")
_TAG_OPEN = re.compile(r"<[a-zA-Z/!]")
_TITLE_OPEN = re.compile(r"<title(?![\w:-])", re.IGNORECASE)
_TITLE_CLOSE = re.compile(r"</title\s*>", re.IGNORECASE)
_BLANK = " \t\n\r\f\v\ufeff"


def quote_source(source: Path | None) -> str:
    """Return `source` as a quoted argument of the boundary tag, `""` for none."""
    return '""' if source is None else f'"{quote(str(source), safe="/")}"'


def is_quoted(bit: str) -> bool:
    """Return whether a boundary tag argument is a quoted source."""
    return len(bit) > 1 and bit[0] == bit[-1] == '"'


def unquote_source(bit: str) -> Path | None:
    """Return the source a quoted boundary tag argument names, `None` for none."""
    text = unquote(bit[1:-1])
    return Path(text) if text else None


def _tokens(content: str) -> Iterator[tuple[TokenType, str, int, int]]:
    """Yield the type, the contents and the bounds of every token Django lexes."""
    for token in DebugLexer(content).tokenize():
        # The debug lexer records token bounds that the plain lexer leaves unset.
        start, end = cast("tuple[int, int]", token.position)
        yield token.token_type, token.contents, start, end


def _command(contents: str) -> str:
    """Return the first word of a block tag, the name Django dispatches it by."""
    head = contents.split(maxsplit=1)
    return head[0] if head else ""


def _parsed_tokens(content: str) -> Iterator[tuple[TokenType, str, int, int]]:
    """Yield every token a parse of `content` reaches, skipping `{% comment %}` blocks.

    Django ends the block only at a bare `endcomment`, so this does too.
    """
    commented = False
    for token in _tokens(content):
        token_type, contents = token[0], token[1]
        if commented:
            commented = not (
                token_type is TokenType.BLOCK and contents == _END_COMMENT_TAG
            )
        elif token_type is TokenType.BLOCK and _command(contents) == _COMMENT_TAG:
            commented = True
        else:
            yield token


def placeholder_spans(content: str) -> list[tuple[int, int]]:
    """Return the bounds of every placeholder a parse of `content` would reach.

    Django's lexer decides what is a tag, so a `{% verbatim %}` placeholder is text.
    """
    spans: list[tuple[int, int]] = []
    open_at: int | None = None
    for token_type, contents, start, end in _parsed_tokens(content):
        if token_type is not TokenType.BLOCK:
            continue
        command = _command(contents)
        if open_at is not None:
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

    Only `load` tags, comments, ASCII blanks and a BOM may precede the one marker.
    """
    if _ROOT_MARKER_WORDS[0] not in content:
        return RootScan(is_root=False, markers=())
    markers: list[tuple[int, int]] = []
    clean_prefix = True
    first_in_place = False
    for token_type, contents, start, end in _parsed_tokens(content):
        words = contents.split()
        command = words[0] if words else ""
        is_block = token_type is TokenType.BLOCK
        if token_type is TokenType.TEXT:
            clean_prefix = clean_prefix and not contents.strip(_BLANK)
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
    """Whether `content` writes an `<html` tag a browser reads as the document element.

    Text in a `{% comment %}`, a `{% verbatim %}` block or an HTML comment is skipped.
    """
    verbatim: str | None = None
    in_comment = False
    for token_type, contents, _start, _end in _parsed_tokens(content):
        if token_type is TokenType.BLOCK:
            if verbatim is None and _command(contents) == _VERBATIM_TAG:
                verbatim = f"end{contents}"
            elif contents == verbatim:
                verbatim = None
        elif token_type is TokenType.TEXT and verbatim is None:
            visible, in_comment = _outside_html_comments(
                contents, in_comment=in_comment
            )
            if _HTML_OPEN.search(visible):
                return True
    return False


def _verbatim_placeholder(content: str) -> bool:
    """Whether a `{% verbatim %}` block of `content` spells a placeholder."""
    verbatim: str | None = None
    for token_type, contents, _start, _end in _parsed_tokens(content):
        if token_type is TokenType.BLOCK:
            if verbatim is None and _command(contents) == _VERBATIM_TAG:
                verbatim = f"end{contents}"
                continue
            if contents == verbatim:
                verbatim = None
                continue
        if verbatim is not None and _PLACEHOLDER_TEXT.search(contents):
            return True
    return False


def _last(pattern: re.Pattern[str], text: str) -> int:
    """Return where the last match of `pattern` in `text` starts, `-1` for none."""
    found = -1
    for match in pattern.finditer(text):
        found = match.start()
    return found


def slot_problem(content: str) -> str | None:
    """Return where the placeholder of a layout sits that slot markers cannot wrap.

    `None` means the markers fit, or the layout holds no placeholder at all.
    """
    if _verbatim_placeholder(content):
        return "inside a {% verbatim %} block"
    spans = placeholder_spans(content)
    if not spans:
        return None
    start = spans[0][0]
    texts = [
        contents
        for token_type, contents, _begin, end in _parsed_tokens(content)
        if token_type is TokenType.TEXT and end <= start
    ]
    visible, _open = _outside_html_comments("".join(texts), in_comment=False)
    if _last(_TAG_OPEN, visible) > visible.rfind(">"):
        return "inside an HTML attribute"
    if _last(_TITLE_OPEN, visible) > _last(_TITLE_CLOSE, visible):
        return "inside a <title>"
    return None


def _outside_html_comments(text: str, *, in_comment: bool) -> tuple[str, bool]:
    """Return the part of `text` outside HTML comments and whether one stays open.

    A comment can span tokens, since a variable inside it splits the text.
    """
    visible: list[str] = []
    position = 0
    while True:
        if in_comment:
            close = text.find(_HTML_COMMENT_CLOSE, position)
            if close < 0:
                return "".join(visible), True
            position = close + len(_HTML_COMMENT_CLOSE)
            in_comment = False
        else:
            opening = text.find(_HTML_COMMENT_OPEN, position)
            if opening < 0:
                visible.append(text[position:])
                return "".join(visible), False
            visible.append(text[position:opening])
            position = opening + len(_HTML_COMMENT_OPEN)
            in_comment = True


__all__ = [
    "BOUNDARY_END_TAG",
    "BOUNDARY_TAG",
    "FAILED_TAG",
    "NO_WRAPPER",
    "PLACEHOLDER",
    "PLACEHOLDER_CLOSE",
    "PLACEHOLDER_OPEN",
    "ROOT_MARKER",
    "SLOT_END_TAG",
    "SLOT_TAG",
    "RootScan",
    "is_quoted",
    "opens_html_document",
    "placeholder_spans",
    "quote_source",
    "scan_root",
    "slot_problem",
    "unquote_source",
]
