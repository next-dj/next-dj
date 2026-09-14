"""Django signals emitted by the components subsystem."""

from django.dispatch import Signal


component_registered: Signal = Signal(use_caching=True)
components_registered: Signal = Signal(use_caching=True)
component_backend_loaded: Signal = Signal(use_caching=True)
component_rendered: Signal = Signal(use_caching=True)


__all__ = [
    "component_backend_loaded",
    "component_registered",
    "component_rendered",
    "components_registered",
]
