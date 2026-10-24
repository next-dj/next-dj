"""Django signals emitted by realtime broker management."""

from django.dispatch import Signal


broker_backend_loaded: Signal = Signal(use_caching=True)
message_published: Signal = Signal(use_caching=True)


__all__ = ["broker_backend_loaded", "message_published"]
