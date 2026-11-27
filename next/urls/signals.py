"""Django signals emitted by the URL routing subsystem."""

from django.dispatch import Signal


route_registered: Signal = Signal(use_caching=True)
intercept_registered: Signal = Signal(use_caching=True)
routes_reloaded: Signal = Signal(use_caching=True)
page_backend_loaded: Signal = Signal(use_caching=True)


__all__ = [
    "intercept_registered",
    "page_backend_loaded",
    "route_registered",
    "routes_reloaded",
]
