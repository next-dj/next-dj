"""Bootstrap component backends so their components are discovered on app ready."""

from __future__ import annotations

from next.components import components_manager


def install() -> None:
    """Load backends and run component discovery on app ready.

    Discovery populates each backend registry. Unless `LAZY_COMPONENT_MODULES`
    is set it also imports every `component.py` so decorators run before the
    first request.
    """
    components_manager._ensure_backends()
    for backend in components_manager._backends:
        ensure_loaded = getattr(backend, "_ensure_loaded", None)
        if callable(ensure_loaded):
            ensure_loaded()


__all__ = ["install"]
