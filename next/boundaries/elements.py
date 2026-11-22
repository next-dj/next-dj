"""A lexical scan of HTML for the elements a markup prefix leaves open and its roots."""

from __future__ import annotations

import re
from typing import Final


_VOID: Final = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)

_FOREIGN: Final = frozenset({"svg", "math"})
_INTEGRATION: Final = frozenset(
    {"foreignobject", "desc", "annotation-xml", "mi", "mo", "mn", "ms", "mtext"}
)
"""The foreign elements whose children are HTML again, where a slash closes nothing."""

_MARKUP: Final = re.compile(
    r"<!--(?:-?>|.*?(?:--!?>|\Z))"
    r"|<(?P<raw>script|style|textarea|title)(?=[\s/>])"
    r"(?:[^>\"']|\"[^\"]*\"|'[^']*')*>.*?(?:</(?P=raw)\s*>|\Z)"
    r"|<(?P<close>/?)(?P<name>[A-Za-z][^\s/>]*)"
    r"(?P<attrs>(?:[^>\"']|\"[^\"]*\"|'[^']*')*)>",
    re.DOTALL | re.IGNORECASE,
)


def _opens(name: str, attrs: str, *, foreign: bool) -> tuple[bool, bool]:
    """Return whether a start tag stays open and whether its children are foreign.

    A browser honours a self-closing slash only on an `svg` or `math` or inside one.
    """
    if name in _VOID:
        return False, foreign
    own = foreign or name in _FOREIGN
    if own and attrs.rstrip().endswith("/"):
        return False, foreign
    return True, name in _FOREIGN or (foreign and name not in _INTEGRATION)


def open_elements(text: str) -> list[tuple[str, str]]:
    """Return the lowercased name and the attributes of each element `text` leaves open.

    Comments, raw-text elements, void elements and self-closing tags leave none open.
    """
    stack: list[tuple[str, str]] = []
    foreign: list[bool] = [False]
    for match in _MARKUP.finditer(text):
        name = match["name"]
        if name is None:
            continue
        lowered = name.lower()
        if not match["close"]:
            opened, inner = _opens(lowered, match["attrs"], foreign=foreign[-1])
            if opened:
                stack.append((lowered, match["attrs"]))
                foreign.append(inner)
            continue
        for depth in range(len(stack) - 1, -1, -1):
            if stack[depth][0] == lowered:
                del stack[depth:]
                del foreign[depth + 1 :]
                break
    return stack


def roots(body: str) -> tuple[list[int], bool]:
    """Return where the name of each top-level element ends and whether text is loose.

    Loose text is text outside every element, which no root element can carry.
    """
    ends: list[int] = []
    foreign: list[bool] = [False]
    loose = False
    position = 0
    for match in _MARKUP.finditer(body):
        top = len(foreign) == 1
        if top and body[position : match.start()].strip():
            loose = True
        position = match.end()
        tag = match["raw"] or match["name"]
        if tag is None:
            continue
        if match["close"]:
            loose = loose or top
            if not top:
                foreign.pop()
            continue
        if top:
            ends.append(match.start() + 1 + len(tag))
        if match["raw"] is None:
            opened, inner = _opens(tag.lower(), match["attrs"], foreign=foreign[-1])
            if opened:
                foreign.append(inner)
    if len(foreign) == 1 and body[position:].strip():
        loose = True
    return ends, loose


def missed(ends: list[int], *, loose: bool) -> str | None:
    """Return how the roots `roots` found miss a single element, `None` for one."""
    if loose:
        return "loose text"
    if len(ends) == 1:
        return None
    return f"{len(ends)} root elements"


def misrooted(body: str) -> str | None:
    """Return how `body` misses a single root element, `None` when it has one."""
    ends, loose = roots(body)
    return missed(ends, loose=loose)


__all__ = ["misrooted", "missed", "open_elements", "roots"]
