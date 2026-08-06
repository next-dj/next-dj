"""Bootstrap component backends so their components are discovered on app ready."""

from __future__ import annotations

from next.components import components_manager


def install() -> None:
    """Load backends and run component discovery on app ready.

    Discovery populates each backend registry. Unless `LAZY_COMPONENT_MODULES`
    is set it also imports every `component.py` so decorators run before the
    first request.
    """
    for backend in components_manager.backends:
        backend.discover()


__all__ = ["install"]
