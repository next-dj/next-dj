"""Signals emitted by the development server watch layer.

`watch_specs_ready` fires after the reloader resolves the full list of
watch specs. Subscribers can inspect or augment the effective spec set
without subclassing the reloader.
"""

from django.dispatch import Signal


watch_specs_ready: Signal = Signal()
