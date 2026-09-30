"""Django signals emitted by the consent subsystem."""

from django.dispatch import Signal


consent_backend_loaded: Signal = Signal(use_caching=True)


__all__ = ["consent_backend_loaded"]
