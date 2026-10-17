"""Django signals emitted by the boundaries area."""

from django.dispatch import Signal


baseline_refrozen: Signal = Signal(use_caching=True)


__all__ = ["baseline_refrozen"]
