"""A URL pattern regex as the JavaScript regex that matches the same paths, or none.

Only syntax Python and JavaScript read alike without flags is carried. Escaped classes
stay out, since Python reads them over Unicode and JavaScript over ASCII.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass
from typing import Final


_PUNCTUATION: Final = frozenset(string.punctuation)
_FIRST: Final = 0x20
_LAST: Final = 0x7E
_SYNTAX: Final = frozenset("^$*+?{()|.[\\")
_REPEATS: Final = frozenset("*+?{")
_SET_OPERATORS: Final = frozenset({"--", "&&", "~~", "||"})
_ENDS: Final = frozenset({"$", "\\Z"})
_ONCE: Final = frozenset({"?", "??"})
_SLASH: Final = "/"
_SEPARATORS: Final = frozenset({"/", "\\/"})
_RUN: Final = 2
_LAZY: Final = "?"


@dataclass(frozen=True, slots=True)
class Portable:
    """A regex both engines read alike, without its anchors, and the anchors it had.

    `spans` counts unbounded repeats that may cross a slash, a backtrack degree each.
    """

    source: str
    start: bool
    end: bool
    spans: int = 0


class _RefusedError(Exception):
    """Raised where the regex leaves the syntax both engines read alike."""


def _plain(char: str) -> bool:
    """Return whether `char` is printable ASCII."""
    return _FIRST <= ord(char) <= _LAST


def _number(text: str) -> bool:
    """Return whether `text` is a run of ASCII digits."""
    return text.isascii() and text.isdigit()


class _Reader:
    """Read a regex once, left to right, into its JavaScript source."""

    def __init__(self, text: str) -> None:
        """Start before the first character of `text`."""
        self.text = text
        self.at = 0
        self.end = False
        self.forked = False
        self.spans = 0
        self.run = 0

    def peek(self, size: int = 1) -> str:
        """Return the next `size` characters without reading them."""
        return self.text[self.at : self.at + size]

    def take(self) -> str:
        """Read one character, refusing past the end."""
        if self.at >= len(self.text):
            raise _RefusedError
        char = self.text[self.at]
        self.at += 1
        return char

    def alternation(self, *, top: bool) -> tuple[str, bool]:
        """Read branches up to a parenthesis, and whether one repeats or forks."""
        source, busy = self.sequence(top=top)
        branches = [source]
        while self.peek() == "|":
            self.at += 1
            source, _busy = self.sequence(top=top)
            branches.append(source)
            busy = True
        if top:
            self.forked = len(branches) > 1
        return "|".join(branches), busy

    def sequence(self, *, top: bool) -> tuple[str, bool]:
        """Read atoms and their quantifiers up to a fork, a parenthesis or the end."""
        parts: list[str] = []
        busy = False
        while self.at < len(self.text) and self.peek() not in {"|", ")"}:
            if top and self.text[self.at :] in _ENDS:
                self.at = len(self.text)
                self.end = True
                break
            atom, inner = self.atom()
            quantifier = self.quantifier()
            if inner and quantifier and quantifier not in _ONCE:
                raise _RefusedError
            busy = busy or inner or bool(quantifier)
            if quantifier[:1] in {"*", "+"} or quantifier.rstrip("?").endswith(",}"):
                self.repeat(atom)
            elif top and atom in _SEPARATORS and not quantifier:
                self.run = 0
            parts.append(atom + quantifier)
        return "".join(parts), busy

    def repeat(self, atom: str) -> None:
        """Count an unbounded repeat of `atom`, refusing a third within one segment."""
        if atom[0] == "(" or re.fullmatch(atom, _SLASH) is not None:
            self.spans += 1
            return
        self.run += 1
        if self.run > _RUN:
            raise _RefusedError

    def atom(self) -> tuple[str, bool]:
        """Read one atom, and whether it is a group that repeats or forks inside."""
        char = self.take()
        if char == "(":
            return self.group()
        if char == "[":
            return self.charset(), False
        if char == "\\":
            return self.escape(), False
        if char == ".":
            return char, False
        if char in _SYNTAX or not _plain(char):
            raise _RefusedError
        return (f"\\{char}" if char in {"]", "}"} else char), False

    def escape(self) -> str:
        """Read an escape, carried only for ASCII punctuation."""
        char = self.take()
        if char not in _PUNCTUATION:
            raise _RefusedError
        return f"\\{char}"

    def group(self) -> tuple[str, bool]:
        """Read a plain, non-capturing or named group as a non-capturing one."""
        if self.peek(2) == "?:":
            self.at += 2
        elif self.peek(3) == "?P<":
            close = self.text.find(">", self.at)
            if close < 0 or not self.text[self.at + 3 : close].isidentifier():
                raise _RefusedError
            self.at = close + 1
        elif self.peek() == "?":
            raise _RefusedError
        source, busy = self.alternation(top=False)
        self.take()
        return f"(?:{source})", busy

    def charset(self) -> str:
        """Read a class of literals, ranges and escaped punctuation."""
        parts = ["^"] if self.peek() == "^" else []
        self.at += len(parts)
        if self.peek() == "]":
            raise _RefusedError
        while self.peek() != "]":
            if self.peek(2) in _SET_OPERATORS:
                raise _RefusedError
            parts.append(self.member())
            if self.peek(2) in _SET_OPERATORS:
                raise _RefusedError
            if self.peek() == "-" and self.peek(2) != "-]":
                self.at += 1
                parts.append("-" + self.member())
                if self.peek() == "-" and self.peek(2) != "-]":
                    raise _RefusedError
        self.at += 1
        return "[" + "".join(parts) + "]"

    def member(self) -> str:
        """Read one character of a class, its punctuation escaped."""
        char = self.take()
        if char == "\\":
            return self.escape()
        if char == "[" or not _plain(char):
            raise _RefusedError
        return f"\\{char}" if char in _PUNCTUATION else char

    def quantifier(self) -> str:
        """Read the quantifier after an atom, empty when there is none."""
        char = self.peek()
        if char == "{":
            found = self.bounds()
        elif char in _REPEATS:
            self.at += 1
            found = char
        else:
            return ""
        if self.peek() == _LAZY:
            self.at += 1
            found += _LAZY
        if self.peek() in _REPEATS:
            raise _RefusedError
        return found

    def bounds(self) -> str:
        """Read a `{m}`, `{m,}` or `{m,n}` quantifier."""
        close = self.text.find("}", self.at)
        inside = self.text[self.at + 1 : close] if close > 0 else ""
        low, _comma, high = inside.partition(",")
        if not _number(low) or (high and not _number(high)):
            raise _RefusedError
        self.at = close + 1
        return "{" + inside + "}"


def translate(regex: str) -> Portable | None:
    """Return `regex` as a source JavaScript reads alike, `None` where unsure.

    A group repeated more than once may not repeat or fork inside, and a segment holds
    two unbounded repeats at most, so no source the table carries backtracks far.
    """
    start = regex.startswith(("^", "\\A"))
    skip = (1 if regex.startswith("^") else 2) if start else 0
    reader = _Reader(regex[skip:])
    try:
        source, _busy = reader.alternation(top=True)
    except _RefusedError:
        return None
    if reader.at < len(reader.text) or (reader.forked and (start or reader.end)):
        return None
    return Portable(source, start, reader.end, reader.spans)


__all__ = ["Portable", "translate"]
