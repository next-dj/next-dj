"""The pluggable cut, chrome digest and prefetch shed of the client router."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Final, override

from next.ports import server_backlog

from .digest import chrome_digest, normalise
from .markers import Cut, CutKind, Divergence


if TYPE_CHECKING:
    from pathlib import Path

    from django.http import HttpRequest

    from .markers import ClientLayouts, LayoutKey, ScannedLayout, ScannedPage


_HALF: Final = 16


class ChromeDigest:
    """Normalise the chrome of one layout and digest it, the shipped digest.

    A subclass strips a per-response token of its own on top of the default.
    """

    def normalise(self, text: str, response_id: str) -> str:
        """Return `text` without what changes per response, `response_id` among it."""
        return normalise(text, response_id)

    def digest(self, text: str) -> str:
        """Return the digest of normalised chrome, 16 hex characters."""
        return chrome_digest(text)


class NavigationCut(ABC):
    """Decide where a navigation cuts the target page against the client's chain."""

    @abstractmethod
    def cut(self, declared: ClientLayouts | None, shell: ScannedPage) -> Cut:
        """Return the cut of `shell` for `declared`, `None` naming no declaration."""


class VerifiedCut(NavigationCut):
    """Keep the deepest layout whose id and digest match the declaration from the root.

    Without a match the body morphs while the root and its head agree, else the page
    loads hard, and a missing or unreadable declaration morphs the body.
    """

    @override
    def cut(self, declared: ClientLayouts | None, shell: ScannedPage) -> Cut:
        """Return the slot, body or visit cut, naming where the chain diverged."""
        early = _unrooted(declared, shell)
        if early is not None or declared is None:
            return early or Cut(CutKind.BODY)
        layouts = shell.layouts
        keys = declared.keys
        kept, diverged = _matched(layouts, keys)
        at, reason = (None, None) if diverged is None else diverged
        if kept:
            return Cut(CutKind.SLOT, kept, at, reason)
        root = layouts[0]
        if root.head is not None and keys[0].digest[:_HALF] != root.head:
            return Cut(CutKind.VISIT, diverged_at=root.ident, reason=Divergence.HEAD)
        return Cut(CutKind.BODY, diverged_at=at, reason=reason)


class PrefetchShed(ABC):
    """Decide whether a prefetch yields to the work the server already holds.

    A shed prefetch answers 429, and the client backs off before it prefetches again.
    """

    @abstractmethod
    def waited(self, request: HttpRequest, page_path: Path) -> float | None:
        """Return how long work waits now when `request` yields, `None` to serve it."""


class LoadShed(PrefetchShed):
    """Shed a prefetch while the deferred work of the process waits past its timeout.

    A project without the boundary feature binds no server load, so nothing is shed.
    """

    @override
    def waited(self, request: HttpRequest, page_path: Path) -> float | None:
        """Return the backlog the server load reports, `None` while it keeps up."""
        return server_backlog()


def _unrooted(declared: ClientLayouts | None, shell: ScannedPage) -> Cut | None:
    """Return the cut decided before any chrome compares, `None` to compare."""
    if declared is None:
        return Cut(CutKind.BODY, diverged_at=shell.root, reason=Divergence.DECLARATION)
    layouts = shell.layouts
    keys = declared.keys
    if not layouts and not keys:
        return Cut(CutKind.BODY)
    if not layouts or not keys or keys[0].ident != layouts[0].ident:
        return Cut(CutKind.VISIT, diverged_at=shell.root, reason=Divergence.ID)
    return None


def _matched(
    layouts: tuple[ScannedLayout, ...], keys: tuple[LayoutKey, ...]
) -> tuple[tuple[str, ...], tuple[str, Divergence] | None]:
    """Return the ids matched from the root down and where the walk stopped."""
    kept: list[str] = []
    for layout, key in zip(layouts, keys, strict=False):
        if key.ident != layout.ident:
            return tuple(kept), (layout.ident, Divergence.ID)
        if key.digest != layout.digest:
            return tuple(kept), (layout.ident, Divergence.DIGEST)
        kept.append(layout.ident)
    if len(kept) < len(layouts):
        return tuple(kept), (layouts[len(kept)].ident, Divergence.DECLARATION)
    return tuple(kept), None


__all__ = ["ChromeDigest", "LoadShed", "NavigationCut", "PrefetchShed", "VerifiedCut"]
