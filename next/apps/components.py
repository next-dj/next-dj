"""Bootstrap component backends so their modules are imported on app ready."""

from __future__ import annotations

from next.components import components_manager


def install() -> None:
    """Load backends and import every registered component module."""
    components_manager._ensure_backends()
    for backend in components_manager._backends:
        if hasattr(backend, "import_all_component_modules"):
            backend.import_all_component_modules()


__all__ = ["install"]
