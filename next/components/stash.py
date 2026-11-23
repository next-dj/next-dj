"""The `<template>` stashes of loading UI a zone reload swaps in on the client.

Rows of one site without a key share one boundary key, so they share one stash.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Final

from django.utils.html import escape


if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.template import Context

    from next.defer import DeferredCall


STASH_ATTR: Final = "data-next-loading"
"""The attribute naming the zone or the boundary key a `<template>` stash serves."""

_TEMPLATE_TAG: Final = re.compile(r"<(/?)template(?=[\s/>]|$)", re.IGNORECASE)
_TEXT_LT: Final = "&lt;"
_CLOSE: Final = "</template>"

_stashed: ContextVar[set[str] | None] = ContextVar("next_loading_stash", default=None)
_loading: ContextVar[bool] = ContextVar("next_loading_content", default=False)


def in_loading() -> bool:
    """Whether the render is inside loading content, which nothing assembles later."""
    return _loading.get()


@contextmanager
def loading_content() -> Iterator[None]:
    """Render the block as loading content, where a deferred call renders in place."""
    token = _loading.set(True)
    try:
        yield
    finally:
        _loading.reset(token)


def _contained(html: str) -> str:
    """Return `html` with no `</template` able to close the stash around it.

    A nested template stays, an unmatched end tag turns to text, an open one is closed.
    """
    if _TEMPLATE_TAG.search(html) is None:
        return html
    depth = 0

    def balance(found: re.Match[str]) -> str:
        nonlocal depth
        if not found[1]:
            depth += 1
        elif depth:
            depth -= 1
        else:
            return _TEXT_LT + found[0][1:]
        return found[0]

    balanced = _TEMPLATE_TAG.sub(balance, html)
    return balanced + _CLOSE * depth


def stash_markup(name: str, html: str, attr: str = STASH_ATTR) -> str:
    """Return the `<template>` element holding `html` as the loading UI of `name`."""
    return f'<template {attr}="{escape(name)}">{_contained(html)}</template>'


@contextmanager
def stashing() -> Iterator[None]:
    """Stash the loading UI of every inline boundary the block renders.

    A nested block joins the outer one, so each key is stashed once.
    """
    if _stashed.get() is not None:
        yield
        return
    token = _stashed.set(set())
    try:
        yield
    finally:
        _stashed.reset(token)


def stash_loading(context: Context, call: DeferredCall) -> str:
    """Return the stash of an inline call inside a `stashing` block, once per key."""
    keys = _stashed.get()
    if keys is None or call.key in keys:
        return ""
    keys.add(call.key)
    html, _failure = call.renderer.loading(context)
    return stash_markup(call.key, html) if html.strip() else ""


__all__ = [
    "STASH_ATTR",
    "in_loading",
    "loading_content",
    "stash_loading",
    "stash_markup",
    "stashing",
]
