"""Django signals emitted across the static pipeline."""

from django.dispatch import Signal


# Receiver caching needs a weak sender, which a frozen slots `StaticAsset` cannot be.
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
