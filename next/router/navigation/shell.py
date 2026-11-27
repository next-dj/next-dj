"""The read of a rendered page, the bounds and the digests of each layout it holds."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from next.router.digest import body_bounds, chrome, find_slot, split_head
from next.router.markers import Cut, CutKind, ScannedLayout, ScannedPage


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from next.router.backends import ChromeDigest


NOTHING: Final = (0, 0)
"""The empty span, the bounds of a layout read from no render."""

NONE_FIXED: Final[Mapping[str, tuple[str, str | None]]] = {}
"""No fixed chrome, so every layout is digested from the render."""


def _digests(
    text: str, response_id: str, backend: ChromeDigest, *, root: bool
) -> tuple[str, str | None]:
    """Return the digest of one chrome and, for a root opening a document, its head."""
    normal = backend.normalise(text, response_id)
    parts = split_head(normal) if root else None
    if parts is None:
        return backend.digest(normal), None
    head = backend.digest(parts[0])
    return head + backend.digest(parts[1]), head


def scanned_layouts(
    html: str,
    response_id: str,
    ids: Sequence[str],
    backend: ChromeDigest | None,
    fixed: Mapping[str, tuple[str, str | None]] = NONE_FIXED,
) -> tuple[ScannedLayout, ...]:
    """Return the layouts of `ids` that `html` holds, digested by `backend` if any.

    A layout of `fixed` chrome takes the digest its source gave, read from no render.
    """
    layouts: list[ScannedLayout] = []
    region = (0, len(html))
    for ident in ids:
        found = find_slot(html, ident, response_id, region)
        if found is None:
            break
        inner, outer = found
        digest, head = "", None
        held = fixed.get(ident)
        if held is not None:
            digest, head = held
        elif backend is not None:
            text = chrome(html, region, outer)
            digest, head = _digests(text, response_id, backend, root=not layouts)
        layouts.append(ScannedLayout(ident, digest, inner, outer, head))
        region = inner
    return tuple(layouts)


def read_shell(
    html: str, response_id: str, ids: Sequence[str], backend: ChromeDigest | None = None
) -> ScannedPage:
    """Return the layouts of `ids` that `html` holds, nested outer first, digested.

    Without `backend` only the bounds are read. A missing slot ends the chain there.
    """
    layouts = scanned_layouts(html, response_id, ids, backend)
    return ScannedPage(html, response_id, layouts, body=body_bounds(html))


def relocated(
    layouts: tuple[ScannedLayout, ...], response_id: str, html: str
) -> ScannedPage:
    """Return the shell of `html` with the digests `layouts` took before it changed."""
    ids = [layout.ident for layout in layouts]
    moved = scanned_layouts(html, response_id, ids, None)
    held = tuple(
        ScannedLayout(old.ident, old.digest, new.slot, new.outer, old.head)
        for old, new in zip(layouts, moved, strict=False)
    )
    return ScannedPage(html, response_id, held, body=body_bounds(html))


def layout_entries(
    layouts: Sequence[ScannedLayout], response_id: str, kept: int = 0
) -> list[list[str | None]]:
    """Return `[id, digest, response id]` per layout, null for the `kept` ones."""
    return [
        [layout.ident, layout.digest, None if index < kept else response_id]
        for index, layout in enumerate(layouts)
    ]


def layouts_payload(shell: ScannedPage, kept: int = 0) -> list[list[str | None]]:
    """Return `[id, digest, response id]` per layout, null for the `kept` ones."""
    return layout_entries(shell.layouts, shell.response_id, kept)


def swapped(shell: ScannedPage, cut: Cut) -> str:
    """Return the HTML a slot or body cut puts on screen."""
    html = shell.html
    if cut.kind is CutKind.SLOT:
        start, end = shell.layouts[len(cut.kept) - 1].slot
    else:
        start, end = shell.body or (0, len(html))
    return html[start:end]


__all__ = [
    "NONE_FIXED",
    "NOTHING",
    "layout_entries",
    "layouts_payload",
    "read_shell",
    "relocated",
    "scanned_layouts",
    "swapped",
]
