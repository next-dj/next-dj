"""Django signals emitted by the URL routing subsystem."""

from django.dispatch import Signal


route_registered: Signal = Signal(use_caching=True)
router_reloaded: Signal = Signal(use_caching=True)


__all__ = ["route_registered", "router_reloaded"]
