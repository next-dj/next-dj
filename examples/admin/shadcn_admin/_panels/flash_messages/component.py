from typing import Any

from django.contrib.messages import get_messages
from django.http import HttpRequest

from next.components import component


_LEVEL_TO_VARIANT = {
    "success": "success",
    "error": "destructive",
    "warning": "warning",
    "info": "info",
    "debug": "info",
}


@component.context("flashes")
def flashes(request: HttpRequest) -> list[dict[str, Any]]:
    """Drain `messages` for this request and return alert-ready dicts."""
    return [
        {
            "text": str(m),
            "variant": _LEVEL_TO_VARIANT.get(m.level_tag, "info"),
        }
        for m in get_messages(request)
    ]
