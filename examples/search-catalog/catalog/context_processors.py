from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from .providers import parse_filters


if TYPE_CHECKING:
    from django.http import HttpRequest


def active_filters(request: HttpRequest) -> dict[str, Any]:
    """Expose active-filter chips with a precomputed drop URL each.

    Each chip carries `label`, `key`, `value`, and `drop_url`. The
    `drop_url` is the query string that the chip's anchor clicks to,
    reproducing the current URL with that single (key, value) pair
    removed. Templates render the strip with
    `<a href="?{{ chip.drop_url }}">{{ chip.label }}</a>` and never
    look up the URL by hand.
    """
    f = parse_filters(request)
    items: list[tuple[str, str]] = [
        (key, value) for key, values in request.GET.lists() for value in values
    ]
    chips: list[dict[str, str]] = []
    if f.q:
        chips.append(_chip(items, f"Search {f.q}", "q", f.q))
    chips.extend(_chip(items, f"Brand {brand}", "brand", brand) for brand in f.brands)
    if f.price_min is not None:
        chips.append(
            _chip(items, f"From {f.price_min}", "price_min", str(f.price_min)),
        )
    if f.price_max is not None:
        chips.append(
            _chip(items, f"To {f.price_max}", "price_max", str(f.price_max)),
        )
    if f.in_stock:
        chips.append(_chip(items, "In stock", "in_stock", "1"))
    return {
        "active_filters": chips,
        "active_filters_count": len(chips),
    }


def _chip(
    items: list[tuple[str, str]],
    label: str,
    key: str,
    value: str,
) -> dict[str, str]:
    """Return a single chip descriptor including the precomputed drop URL."""
    kept = [(k, v) for k, v in items if (k, v) != (key, value)]
    return {
        "label": label,
        "key": key,
        "value": value,
        "drop_url": urlencode(kept),
    }
