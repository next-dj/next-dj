"""Django signals emitted by the URL routing subsystem."""

from django.dispatch import Signal


route_registered: Signal = Signal(use_caching=True)
intercept_registered: Signal = Signal(use_caching=True)
router_reloaded: Signal = Signal(use_caching=True)
router_backend_loaded: Signal = Signal(use_caching=True)


__all__ = [
    "intercept_registered",
    "route_registered",
    "router_backend_loaded",
    "router_reloaded",
]
