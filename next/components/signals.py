"""Django signals emitted by the components subsystem."""

from __future__ import annotations

from django.dispatch import Signal


component_registered: Signal = Signal()
components_registered: Signal = Signal()
component_backend_loaded: Signal = Signal()
component_rendered: Signal = Signal()


__all__ = [
    "component_backend_loaded",
    "component_registered",
    "component_rendered",
    "components_registered",
]
