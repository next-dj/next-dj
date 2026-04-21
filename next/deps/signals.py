"""Django signals emitted by the dependency-injection layer.

`provider_registered` fires whenever a `RegisteredParameterProvider`
subclass is added to the auto-registry. External code may listen to
the signal to observe provider wiring, typically in tests or
diagnostics.
"""

from __future__ import annotations

from django.dispatch import Signal


provider_registered: Signal = Signal()
"""Emitted when a `RegisteredParameterProvider` subclass registers itself."""
