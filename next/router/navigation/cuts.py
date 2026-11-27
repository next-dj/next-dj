"""The cut a navigation lands on, foreseen from fixed chrome or settled after it."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from next.pages.loaders import layout_ids
from next.router.backends import ChromeDigest, VerifiedCut
from next.router.digest import regions
from next.router.manager import client_router
from next.router.markers import Cut, CutKind, Divergence, ScannedLayout, ScannedPage
from next.router.render import ASSETS_MARK
from next.static.nonce import resolve_nonce
from next.static.runtime import dropped_script

from .clearing import clearing
from .intent import declaration
from .shell import NOTHING, swapped


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from django.http import HttpRequest


def _authored(html: str, response_id: str) -> str:
    """Return `html` without the tags the injector placed, which the runtime handles."""
    parts: list[str] = []
    at = 0
    for start, end in regions(html, ASSETS_MARK, response_id):
        parts.append(html[at:start])
        at = end
    parts.append(html[at:])
    return "".join(parts)


def scripted(request: HttpRequest, cut: Cut, shell: ScannedPage) -> Cut:
    """Return `cut`, a hard load once the swap would drop a script the page runs.

    A document load runs every script of the page, a swap only the inline ones under
    the response nonce, so the page loads rather than lose what its scripts do.
    """
    if cut.kind is CutKind.VISIT:
        return cut
    script = dropped_script(
        _authored(swapped(shell, cut), shell.response_id), resolve_nonce(request)
    )
    if script is None:
        return cut
    return Cut(
        CutKind.VISIT, diverged_at=shell.root, reason=Divergence.SCRIPT, script=script
    )


@dataclass(frozen=True, slots=True)
class Foreseen:
    """The slot cut a navigation lands on, read from fixed chrome before it renders.

    The page then renders the kept slot alone, and its HTML is the whole shell.
    """

    cut: Cut
    layouts: tuple[ScannedLayout, ...]

    def shell(self, html: str, response_id: str) -> ScannedPage:
        """Return the shell of the slot `html` rendered alone, the kept slot whole."""
        slot = self.cut.slot
        bounds = (0, len(html))
        layouts = tuple(
            replace(layout, slot=bounds, outer=bounds)
            if layout.ident == slot
            else layout
            for layout in self.layouts
        )
        return ScannedPage(html, response_id, layouts)

    def settle(self, request: HttpRequest, shell: ScannedPage) -> Cut:
        """Return the cut, a hard load once the render cleared the router.

        The chrome around the slot never rendered, so no body morph can stand in.
        """
        if not clearing(request):
            return scripted(request, self.cut, shell)
        return Cut(CutKind.VISIT, diverged_at=shell.root, reason=Divergence.CLEAR)


def foresee(
    request: HttpRequest, file_path: Path, fixed: Mapping[str, tuple[str, str | None]]
) -> Foreseen | None:
    """Return the slot cut a navigation lands on when fixed chrome decides it alone.

    The shipped cut and digest decide it from source digests, and a clear forbids it.
    """
    if type(client_router.cut()) is not VerifiedCut:
        return None
    if type(client_router.digest()) is not ChromeDigest or clearing(request):
        return None
    ids = layout_ids(file_path)
    if not ids or any(ident not in fixed for ident in ids):
        return None
    layouts = tuple(
        ScannedLayout(ident, fixed[ident][0], NOTHING, NOTHING, fixed[ident][1])
        for ident in ids
    )
    cut = client_router.cut().cut(declaration(request), ScannedPage("", "", layouts))
    if cut.kind is not CutKind.SLOT:
        return None
    return Foreseen(cut, layouts)


def cleared(request: HttpRequest, cut: Cut, shell: ScannedPage) -> Cut:
    """Return `cut`, a kept layout given up for a body morph once the router clears.

    Kept chrome may still show what the session held before it changed.
    """
    if cut.kind is CutKind.SLOT and clearing(request):
        cut = Cut(CutKind.BODY, diverged_at=shell.root, reason=Divergence.CLEAR)
    return scripted(request, cut, shell)


__all__ = ["Foreseen", "cleared", "foresee", "scripted"]
