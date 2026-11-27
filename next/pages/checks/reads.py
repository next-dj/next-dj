"""The names a template reads, over its whole text or the chrome a layout keeps."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from django.template.base import Lexer, Token, TokenType


if TYPE_CHECKING:
    from collections.abc import Iterator


_QUOTED: Final = re.compile(r"\"[^\"]*\"|'[^']*'")
_FILTER: Final = re.compile(r"\|\s*\w+")
_NAME: Final = re.compile(r"(?<![\w.])([A-Za-z_]\w*)")
_COMMENT: Final = "comment"
_END_COMMENT: Final = "endcomment"
_SINGLE_SLOT: Final = "template"
_PAIRS: Final = {"zone": "endzone", "#template": "/template"}
_SLOT: Final = "#template"


def _command(token: Token) -> str:
    """Return the first word of a block tag, the name Django dispatches it by."""
    head = token.contents.split(maxsplit=1)
    return head[0] if head else ""


def _parsed(text: str) -> list[Token]:
    """Return the variable and tag tokens a parse of `text` reaches, past comments."""
    found: list[Token] = []
    commented = False
    for token in Lexer(text).tokenize():
        if token.token_type is TokenType.BLOCK:
            command = _command(token)
            if commented:
                commented = command != _END_COMMENT
                continue
            if command == _COMMENT:
                commented = True
                continue
            found.append(token)
        elif token.token_type is TokenType.VAR and not commented:
            found.append(token)
    return found


def _regions(tokens: list[Token]) -> list[tuple[int, int]]:
    """Return the token bounds of each zone and of the slot fallback of a layout.

    A zone around the slot is no region, as the chrome it wraps is kept chrome.
    """
    opened: dict[str, list[int]] = {closer: [] for closer in _PAIRS.values()}
    pairs: list[tuple[int, int, str]] = []
    slot: int | None = None
    for index, token in enumerate(tokens):
        if token.token_type is not TokenType.BLOCK:
            continue
        command = _command(token)
        if command in _PAIRS:
            opened[_PAIRS[command]].append(index)
            if command == _SLOT:
                slot = index
        elif command == _SINGLE_SLOT:
            slot = index
        elif opened.get(command):
            pairs.append((opened[command].pop(), index, command))
    return [
        (start, end)
        for start, end, closer in pairs
        if closer == _PAIRS[_SLOT] or slot is None or not start < slot < end
    ]


def _chrome(text: str) -> Iterator[Token]:
    """Yield the variable and tag tokens of a layout outside its zones and slot."""
    tokens = _parsed(text)
    regions = _regions(tokens)
    for index, token in enumerate(tokens):
        if not any(start <= index <= end for start, end in regions):
            yield token


def chrome_contents(text: str) -> Iterator[str]:
    """Yield the variable and tag contents of a layout outside its zones and slot."""
    for token in _chrome(text):
        yield token.contents


def _names(token_type: TokenType, contents: str) -> list[str]:
    """Return the names the contents of one variable or tag token start with.

    A filter name is no read, and a name a tag binds itself reads as one it takes in.
    """
    if token_type is TokenType.BLOCK:
        contents = contents.partition(" ")[2]
    return _NAME.findall(_FILTER.sub("|", _QUOTED.sub("", contents)))


def template_names(text: str) -> frozenset[str]:
    """Return the names the variables and tag arguments of a template text read."""
    return frozenset(
        name
        for token in _parsed(text)
        for name in _names(token.token_type, token.contents)
    )


def chrome_names(text: str) -> frozenset[str]:
    """Return the names a layout reads outside its zones, its slot and its comments."""
    return frozenset(
        name
        for token in _chrome(text)
        for name in _names(token.token_type, token.contents)
    )


__all__ = ["chrome_contents", "chrome_names", "template_names"]
