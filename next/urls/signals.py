"""Django signals emitted by the URL routing subsystem."""

from django.dispatch import Signal


route_registered: Signal = Signal()
router_reloaded: Signal = Signal()


__all__ = ["route_registered", "router_reloaded"]
