"""The Speculation Rules a routed document carries for the links the router leaves."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Final

from next.conf.router import RouterScope
from next.static.runtime import SCRIPT_ESCAPES


if TYPE_CHECKING:
    from collections.abc import Sequence

    from next.conf.router import Eagerness


OFF: Final = (
    "[data-next-router=off],"
    "[data-next-reload]:not([data-next-zone]):not([data-next-boundary])"
)
"""What opts a link out of the router, on the link or on an ancestor."""

GUARDED: Final = (
    "[data-next-layer],[data-next-merge],[data-next-fetch],[data-next-retry],"
    "[data-next-confirm],[data-next-prefetch=off]"
)
"""What keeps a link out of every speculation, on the link or on an ancestor."""

_OWNER: Final = "[data-next-router]"
_SAME_ORIGIN: Final = "/*"
_PATTERN_SPECIALS: Final = re.compile(r"([\\:*?+(){}#])")


def _within(selector: str) -> dict[str, object]:
    """Return the condition matching a link `selector` matches or one inside such."""
    return {"selector_matches": f":is({selector}),:is({selector}) *"}


def _prefix(path: str) -> dict[str, object]:
    """Return the condition matching a link whose path starts with `path`."""
    return {"href_matches": _PATTERN_SPECIALS.sub(r"\\\1", path) + "*"}


def rules(
    eagerness: Eagerness, scope: RouterScope, exclude: Sequence[str]
) -> dict[str, object]:
    """Return the prefetch rules for every same-origin link the router does not own."""
    unowned = [_within(OFF), *map(_prefix, exclude)]
    if scope is RouterScope.OPT_IN:
        unowned.append({"not": _within(_OWNER)})
    where = {
        "and": [
            {"href_matches": _SAME_ORIGIN},
            {"or": unowned},
            {"not": _within(GUARDED)},
        ]
    }
    entry = {"source": "document", "where": where, "eagerness": eagerness}
    return {"prefetch": [entry]}


def encoded_rules(
    eagerness: Eagerness, scope: RouterScope, exclude: Sequence[str]
) -> str:
    """Return the rules as JSON safe inside a `<script>` element."""
    found = rules(eagerness, scope, exclude)
    return json.dumps(found, separators=(",", ":")).translate(SCRIPT_ESCAPES)


__all__ = ["GUARDED", "OFF", "encoded_rules", "rules"]
