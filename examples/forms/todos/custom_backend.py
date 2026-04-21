"""Demonstrate a pluggable `FormActionBackend` that records dispatches."""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.forms import RegistryFormActionBackend


if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse


class AuditedRegistryFormActionBackend(RegistryFormActionBackend):
    """Extend `RegistryFormActionBackend` with a per-UID dispatch audit log."""

    def __init__(self) -> None:
        """Initialize the registry and reset the dispatch log."""
        super().__init__()
        self.dispatch_log: list[str] = []

    def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
        """Record the UID before delegating to the parent dispatcher."""
        self.dispatch_log.append(uid)
        return super().dispatch(request, uid)
