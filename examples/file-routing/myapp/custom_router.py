"""Demonstrate a pluggable `RouterBackend` adding a fixed informational route."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.http import HttpResponse
from django.urls import path

from next.urls import FileRouterBackend


if TYPE_CHECKING:
    from django.urls import URLPattern, URLResolver


def _info_view(_request: object) -> HttpResponse:
    return HttpResponse("Served by TaggedFileRouterBackend")


class TaggedFileRouterBackend(FileRouterBackend):
    """Extend `FileRouterBackend` with an extra `/__router_info/` route."""

    def generate_urls(self) -> list[URLPattern | URLResolver]:
        """Return the base file-router patterns plus the info route."""
        patterns: list[URLPattern | URLResolver] = list(super().generate_urls())
        patterns.append(path("__router_info/", _info_view, name="next_router_info"))
        return patterns
