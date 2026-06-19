from __future__ import annotations

from typing import TYPE_CHECKING, Any

from next.forms.wizard import FormWizardBackend


if TYPE_CHECKING:
    from django.http import HttpRequest


class CountingWizardBackend(FormWizardBackend):
    """Counts backend round-trips while delegating to a real backend."""

    def __init__(self, inner: FormWizardBackend) -> None:
        """Wrap a real wizard backend and zero the round-trip counters."""
        self.inner = inner
        self.loads = 0
        self.saves = 0
        self.clears = 0

    def reset_counts(self) -> None:
        self.loads = 0
        self.saves = 0
        self.clears = 0

    def load(self, request: HttpRequest, storage_id: str) -> dict[str, Any]:
        self.loads += 1
        return self.inner.load(request, storage_id)

    def save_step(
        self, request: HttpRequest, storage_id: str, step: str, data: dict[str, Any]
    ) -> None:
        self.saves += 1
        self.inner.save_step(request, storage_id, step, data)

    def clear(self, request: HttpRequest, storage_id: str) -> None:
        self.clears += 1
        self.inner.clear(request, storage_id)
