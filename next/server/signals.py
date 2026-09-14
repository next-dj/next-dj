"""Signals emitted by the development server watch layer.

`watch_specs_ready` fires with the resolved spec list, so a subscriber can augment it.
"""

from django.dispatch import Signal


watch_specs_ready: Signal = Signal(use_caching=True)
