"""Django signals emitted by the permissions subsystem."""

from django.dispatch import Signal


permission_registered: Signal = Signal(use_caching=True)


__all__ = ["permission_registered"]
