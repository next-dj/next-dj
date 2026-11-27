"""Django signals emitted by the compression area."""

from django.dispatch import Signal


compression_backend_loaded: Signal = Signal(use_caching=True)


__all__ = ["compression_backend_loaded"]
