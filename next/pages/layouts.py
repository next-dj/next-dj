"""Layout identity, the region markers of the client router and the per-page id chain.

The router reads a rendered page through these markers, so their grammar lives here.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, NewType

from next.boundaries.ids import ID_BYTES
from next.caches import PageCache
from next.requests import held_next, next_of
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

_DIGEST_BYTES: Final = 8


def opening(kind: str, response_id: str, ident: str = "") -> str:
    """Return the comment that opens an enclosed region of `kind`."""
    return (
        f"<!--{kind} {ident} {response_id}-->"
        if ident
        else f"<!--{kind} {response_id}-->"
    )


def closing(kind: str, response_id: str, ident: str = "") -> str:
    """Return the comment that closes an enclosed region of `kind`."""
    return (
        f"<!--/{kind} {ident} {response_id}-->"
        if ident
        else f"<!--/{kind} {response_id}-->"
    )


def enclosed(kind: str, response_id: str, html: str, ident: str = "") -> str:
    """Return `html` between the enclosing comments of `kind`."""
    return (
        f"{opening(kind, response_id, ident)}{html}{closing(kind, response_id, ident)}"
    )


def mint_response_id(request: HttpRequest) -> str:
    """Return the response id of `request`, minting it on the first call.

    Only a view of a page with the router on mints one, so markers render nowhere else.
    """
    namespace = next_of(request)
    held = namespace.response_id
    if held is not None:
        return held
    response_id = namespace.response_id = secrets.token_hex(ID_BYTES)
    return response_id


def request_response_id(request: object) -> str | None:
    """Return the response id `request` carries, `None` for a render without one."""
    held = held_next(request)
    return None if held is None else held.response_id


def context_response_id(context: Context) -> str | None:
    """Return the response id of the request a template `context` renders for."""
    return request_response_id(context.get(REQUEST_KEY))


def enclose_region(context: Context, kind: str, html: str) -> str:
    """Return `html` as an enclosed region of `kind` when a response id is minted."""
    response_id = context_response_id(context)
    return html if response_id is None else enclosed(kind, response_id, html)


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
    digest = hashlib.blake2b(digest_size=_DIGEST_BYTES)
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


_CHAINS: PageCache[Path, LayoutChain] = PageCache(depends=())


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
    "SLOT",
    "ZONE",
    "LayoutChain",
    "LayoutId",
    "closing",
    "context_response_id",
    "enclose_region",
    "enclosed",
    "forget_chains",
    "held_chain",
    "hold_chain",
    "layout_id",
    "mint_response_id",
    "opening",
    "request_response_id",
]
