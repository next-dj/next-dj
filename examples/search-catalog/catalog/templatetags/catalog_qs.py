from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django import template


register = template.Library()


@register.simple_tag(takes_context=True)
def querystring(context: dict[str, Any], **overrides: object) -> str:
    """Return `request.GET` as a query string with the given overrides applied.

    Existing keys in `overrides` replace their values entirely. Other
    keys are copied over preserving repeated entries. Values set to
    `None` are dropped from the resulting query string.
    """
    request = context.get("request")
    if request is None:
        return urlencode({k: v for k, v in overrides.items() if v is not None})
    items: list[tuple[str, str]] = [
        (key, value)
        for key, values in request.GET.lists()
        if key not in overrides
        for value in values
    ]
    for key, value in overrides.items():
        if value is None:
            continue
        items.append((key, str(value)))
    return urlencode(items)
