"""The layout placeholder grammar and the scan that locates it in a layout source.

Composition and the layout checks share these spellings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from django.template.base import DebugLexer, TokenType


if TYPE_CHECKING:
    from collections.abc import Iterator


# The canonical spellings, written by composition and named by the checks.
PLACEHOLDER = "{% template %}"
PLACEHOLDER_OPEN = "{% #template %}"
PLACEHOLDER_CLOSE = "{% /template %}"

_SINGLE_TAG = "template"
_OPEN_TAG = "#template"
_CLOSE_TAG = "/template"
_COMMENT_TAG = "comment"
_END_COMMENT_TAG = "endcomment"


def _block_tags(content: str) -> Iterator[tuple[str, int, int]]:
    """Yield the command and the bounds of every block tag Django lexes in `content`.

    A tag written under `{% verbatim %}` is text to the lexer, so it never comes out.
    """
    for token in DebugLexer(content).tokenize():
        if token.token_type is not TokenType.BLOCK:
            continue
        head = token.contents.split(maxsplit=1)
        # The debug lexer records token bounds that the plain lexer leaves unset.
        start, end = cast("tuple[int, int]", token.position)
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


__all__ = ["PLACEHOLDER", "PLACEHOLDER_CLOSE", "PLACEHOLDER_OPEN", "placeholder_spans"]
