"""Django signals emitted across the static pipeline."""

from django.dispatch import Signal


# A cached signal keys its receiver lookup on a weak reference to the sender.
# `asset_registered` sends a frozen slots `StaticAsset`, which has no `__weakref__`, and
# `collector_finalized` sends a collector built fresh for one render, so a sender-keyed
# entry could never be hit again. Both stay uncached.
asset_registered: Signal = Signal()
collector_finalized: Signal = Signal()
html_injected: Signal = Signal(use_caching=True)
backend_loaded: Signal = Signal(use_caching=True)
