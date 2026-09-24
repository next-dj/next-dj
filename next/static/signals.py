"""Django signals emitted across the static pipeline."""

from django.dispatch import Signal


# A cached signal keys receiver lookup on a weak sender reference, which a frozen slots
# `StaticAsset` cannot carry and a per-render collector could never hit twice.
asset_registered: Signal = Signal()
collector_finalized: Signal = Signal()
html_injected: Signal = Signal(use_caching=True)
static_backend_loaded: Signal = Signal(use_caching=True)


__all__ = [
    "asset_registered",
    "collector_finalized",
    "html_injected",
    "static_backend_loaded",
]
