"""Django signals emitted by the scripts subsystem."""

from django.dispatch import Signal


scripts_registered: Signal = Signal(use_caching=True)


__all__ = ["scripts_registered"]
