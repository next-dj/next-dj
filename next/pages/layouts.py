"""Layout identity, the sealed markers of the client router and the per-page id chain.

The router reads a rendered page through these markers, so their grammar lives here.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, NewType

from next.caches import PageCache
from next.seeding import REQUEST_KEY
from next.stamps import all_fresh
from next.utils import template_edits_watched


if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from django.http import HttpRequest
    from django.template.context import Context


LayoutId = NewType("LayoutId", str)
"""The opaque id of one layout file, stable across processes until its source moves."""

SLOT: Final = "nx:s"
"""The marker kind around the slot one layout wraps, followed by the layout id."""

ZONE: Final = "nx:z"
"""The marker kind around one rendered zone, whose body chrome digests leave out."""

META: Final = "nx:m"
"""The marker kind around the head tags `{% metadata %}` renders."""

ASSETS: Final = "nx:a"
"""The marker kind around the tags the static injector writes into a placeholder."""

SEAL_ATTR: Final = "_next_router_seal"
_SEAL_BYTES: Final = 4
_ID_BYTES: Final = 8


def opening(kind: str, seal: str, ident: str = "") -> str:
    """Return the comment that opens a sealed region of `kind`."""
    return f"<!--{kind} {ident} {seal}-->" if ident else f"<!--{kind} {seal}-->"


def closing(kind: str, seal: str, ident: str = "") -> str:
    """Return the comment that closes a sealed region of `kind`."""
    return f"<!--/{kind} {ident} {seal}-->" if ident else f"<!--/{kind} {seal}-->"


def sealed(kind: str, seal: str, html: str, ident: str = "") -> str:
    """Return `html` between the sealed comments of `kind`."""
    return f"{opening(kind, seal, ident)}{html}{closing(kind, seal, ident)}"


def mint_seal(request: HttpRequest) -> str:
    """Return the router seal of `request`, minting it on the first call.

    Only a view of a page with the router on mints one, so markers render nowhere else.
    """
    held = getattr(request, SEAL_ATTR, None)
    if isinstance(held, str):
        return held
    seal = secrets.token_hex(_SEAL_BYTES)
    setattr(request, SEAL_ATTR, seal)
    return seal


def request_seal(request: object) -> str | None:
    """Return the router seal `request` carries, `None` for a render without one."""
    held = getattr(request, SEAL_ATTR, None)
    return held if isinstance(held, str) else None


def context_seal(context: Context) -> str | None:
    """Return the router seal of the request a template `context` renders for."""
    return request_seal(context.get(REQUEST_KEY))


def seal_region(context: Context, kind: str, html: str) -> str:
    """Return `html` sealed as a region of `kind` when the render carries a seal."""
    seal = context_seal(context)
    return html if seal is None else sealed(kind, seal, html)


def _relative(layout_file: Path, roots: Iterable[Path]) -> str:
    """Return the path of `layout_file` below the deepest root holding it."""
    holding = [root for root in roots if layout_file.is_relative_to(root)]
    if not holding:
        return layout_file.as_posix()
    root = max(holding, key=lambda found: len(found.parts))
    return f"{root.name}/{layout_file.relative_to(root).as_posix()}"


def layout_id(layout_file: Path, source: str, roots: Iterable[Path]) -> LayoutId:
    """Return the id of a layout from its path below its root and its source.

    An edit or a deploy that changes the source changes the id, so a stale tab diverges.
    """
    digest = hashlib.blake2b(digest_size=_ID_BYTES)
    digest.update(_relative(layout_file, roots).encode())
    digest.update(b"\x00")
    digest.update(source.encode())
    return LayoutId(digest.hexdigest())


@dataclass(frozen=True, slots=True)
class LayoutChain:
    """The ids of the layouts one page composes through, outer first, with the stamps.

    `version` is the module memo version the walk ran under.
    """

    ids: tuple[LayoutId, ...]
    stamps: tuple[tuple[Path, int | None], ...]
    version: int


_CHAINS: PageCache[Path, LayoutChain] = PageCache()


def held_chain(file_path: Path, version: int) -> LayoutChain | None:
    """Return the chain held for `file_path` while it is valid, else `None`.

    A process watching template edits compares the layout stamps too.
    """
    held = _CHAINS.get(file_path)
    if held is None or held.version != version:
        return None
    if template_edits_watched() and not all_fresh(held.stamps):
        return None
    return held


def hold_chain(file_path: Path, chain: LayoutChain) -> None:
    """Hold the chain of `file_path` beside the composed skeleton."""
    _CHAINS[file_path] = chain


def forget_chains() -> None:
    """Drop every held chain."""
    _CHAINS.clear()


__all__ = [
    "ASSETS",
    "META",
    "SEAL_ATTR",
    "SLOT",
    "ZONE",
    "LayoutChain",
    "LayoutId",
    "closing",
    "context_seal",
    "forget_chains",
    "held_chain",
    "hold_chain",
    "layout_id",
    "mint_seal",
    "opening",
    "request_seal",
    "seal_region",
    "sealed",
]
