"""The Speculation Rules a routed document carries for the links the router leaves."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Final

from next.boundaries.markup import RETRY_ATTR
from next.conf.router import RouterScope
from next.partial.keys import (
    BOUNDARY_ATTR,
    CONFIRM_ATTR,
    FETCH_ATTR,
    PREFETCH_ATTR,
    ROUTER_ATTR,
)
from next.partial.layers import LAYER_ATTR
from next.partial.more import MERGE_ATTR
from next.partial.zone import RELOAD_ATTR, ZONE_ATTR
from next.static.runtime import SCRIPT_ESCAPES


if TYPE_CHECKING:
    from collections.abc import Sequence

    from next.conf.router import Eagerness


OFF: Final = (
    f"[{ROUTER_ATTR}=off],[{RELOAD_ATTR}]:not([{ZONE_ATTR}]):not([{BOUNDARY_ATTR}])"
)
"""What opts a link out of the router, on the link or on an ancestor."""

GUARDED: Final = (
    f"[{LAYER_ATTR}],[{MERGE_ATTR}],[{FETCH_ATTR}],[{RETRY_ATTR}],"
    f"[{CONFIRM_ATTR}],[{PREFETCH_ATTR}=off]"
)
"""What keeps a link out of every speculation, on the link or on an ancestor."""

_OWNER: Final = f"[{ROUTER_ATTR}]"
_SAME_ORIGIN: Final = "/*"
_PATTERN_SPECIALS: Final = re.compile(r"([\\:*?+(){}#])")


def _within(selector: str) -> dict[str, object]:
    """Return the condition matching a link `selector` matches or one inside such."""
    return {"selector_matches": f":is({selector}),:is({selector}) *"}


def _prefix(path: str) -> dict[str, object]:
    """Return the condition matching a link whose path starts with `path`."""
    return {"href_matches": _PATTERN_SPECIALS.sub(r"\\\1", path) + "*"}


def rules(
    eagerness: Eagerness,
    scope: RouterScope,
    exclude: Sequence[str],
    pages: Sequence[str] = (),
    others: Sequence[str] = (),
) -> dict[str, object]:
    """Return the prefetch rules for every same-origin link the router does not own.

    Under the `pages` scope a link counts only while it matches a page of `pages` and no
    route of `others`, the routes that may take a path from a page.
    """
    unowned = [_within(OFF), *map(_prefix, exclude)]
    if scope is RouterScope.OPT_IN:
        unowned.append({"not": _within(_OWNER)})
    paths: list[dict[str, object]] = [
        {"href_matches": list(pages) if scope is RouterScope.PAGES else _SAME_ORIGIN}
    ]
    if scope is RouterScope.PAGES and others:
        paths.append({"not": {"href_matches": list(others)}})
    where = {"and": [*paths, {"or": unowned}, {"not": _within(GUARDED)}]}
    entry = {"source": "document", "where": where, "eagerness": eagerness}
    return {"prefetch": [entry]}


def encoded_rules(
    eagerness: Eagerness,
    scope: RouterScope,
    exclude: Sequence[str],
    pages: Sequence[str] = (),
    others: Sequence[str] = (),
) -> str:
    """Return the rules as JSON safe inside a `<script>` element."""
    found = rules(eagerness, scope, exclude, pages, others)
    return json.dumps(found, separators=(",", ":")).translate(SCRIPT_ESCAPES)


__all__ = ["GUARDED", "OFF", "encoded_rules", "rules"]
