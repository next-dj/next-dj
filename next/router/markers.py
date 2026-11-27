"""Value objects of the client router, its declarations, scanned layouts and cuts."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Final


MAX_LAYOUTS: Final = 32
"""The most layouts a declaration lists, past which the client declares none."""


class CutKind(enum.StrEnum):
    """The rung a navigation lands on, a slot morph, a body morph or a hard load."""

    SLOT = "slot"
    BODY = "body"
    VISIT = "visit"


class Divergence(enum.StrEnum):
    """Why the walk down the declared chain stopped at a layout."""

    ID = "id"
    DIGEST = "digest"
    HEAD = "head"
    DECLARATION = "declaration"
    CLEAR = "clear"
    SCRIPT = "script"


@dataclass(frozen=True, slots=True)
class LayoutKey:
    """One layout the client declares on screen, its id and its chrome digest."""

    ident: str
    digest: str


@dataclass(frozen=True, slots=True)
class ClientLayouts:
    """The layouts the client has on screen, outer first, parsed from its header."""

    keys: tuple[LayoutKey, ...]


@dataclass(frozen=True, slots=True)
class ScannedLayout:
    """One layout found in a rendered page by its enclosing slot markers.

    `slot` bounds the inner HTML of its slot, `outer` the slot with both markers, and
    `head` is the digest of the part before `<body` for a layout opening the document.
    """

    ident: str
    digest: str
    slot: tuple[int, int]
    outer: tuple[int, int]
    head: str | None = None


@dataclass(frozen=True, slots=True)
class ScannedPage:
    """A rendered page as the router reads it, its layouts outer first.

    `body` bounds the children of `<body>`, `None` when the page writes no body.
    """

    html: str
    response_id: str
    layouts: tuple[ScannedLayout, ...]
    body: tuple[int, int] | None = None

    @property
    def root(self) -> str | None:
        """Return the id of the outermost layout, `None` for a page without one."""
        return self.layouts[0].ident if self.layouts else None


@dataclass(frozen=True, slots=True)
class Cut:
    """Where a navigation cuts the page, with the layouts it keeps on screen.

    `diverged_at` names the first layout that did not match, with the `reason`, and
    `script` the tag a swap would have dropped when that is the reason.
    """

    kind: CutKind
    kept: tuple[str, ...] = ()
    diverged_at: str | None = None
    reason: Divergence | None = None
    script: str | None = None

    @property
    def slot(self) -> str | None:
        """Return the id of the layout whose slot the response replaces."""
        return self.kept[-1] if self.kind is CutKind.SLOT and self.kept else None


__all__ = [
    "MAX_LAYOUTS",
    "ClientLayouts",
    "Cut",
    "CutKind",
    "Divergence",
    "LayoutKey",
    "ScannedLayout",
    "ScannedPage",
]
