"""What a navigation request declares, its layouts on screen, its origin and its URL."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from next.forms.uid import validated_origin_path
from next.partial.headers import partial_intent
from next.router.markers import MAX_LAYOUTS, ClientLayouts, LayoutKey


if TYPE_CHECKING:
    from django.http import HttpRequest


DECLARATION_BYTES: Final = 1024
"""The size past which a layout declaration is ignored, as one never sent whole."""

_KEY: Final = re.compile(r"([0-9a-f]{16})\.([0-9a-f]{16}|[0-9a-f]{32})")


def parse_declaration(raw: str | None, limit: int) -> ClientLayouts | None:
    """Return the layouts the client declares, `None` for an absent or unusable value.

    A value past `limit` entries or `DECLARATION_BYTES` counts as no declaration.
    """
    if raw is None or len(raw.encode()) > DECLARATION_BYTES:
        return None
    if not raw.strip():
        return ClientLayouts(())
    parts = raw.split(",")
    if len(parts) > limit:
        return None
    found: list[LayoutKey] = []
    for part in parts:
        match = _KEY.fullmatch(part.strip())
        if match is None:
            return None
        found.append(LayoutKey(match[1], match[2]))
    return ClientLayouts(tuple(found))


def declaration(request: HttpRequest) -> ClientLayouts | None:
    """Return the layouts the navigation of `request` declares on screen."""
    raw = partial_intent(request).layouts
    return parse_declaration(raw, MAX_LAYOUTS)


def origin_path(request: HttpRequest) -> str | None:
    """Return the same-site path the client names in `X-Next-Origin`, if any."""
    return validated_origin_path(partial_intent(request).origin)


def target_href(request: HttpRequest) -> str:
    """Return the path and query of `request` with its leading slashes run into one."""
    return "/" + request.get_full_path().lstrip("/")


def hostlike(request: HttpRequest) -> bool:
    """Whether the path of `request` opens with `//`, which browsers read as a host."""
    return request.get_full_path().startswith("//")


__all__ = [
    "DECLARATION_BYTES",
    "declaration",
    "hostlike",
    "origin_path",
    "parse_declaration",
    "target_href",
]
