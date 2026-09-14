"""Django signals emitted by the dependency-injection layer."""

from django.dispatch import Signal


provider_registered: Signal = Signal()
"""Emitted when a `RegisteredParameterProvider` subclass registers itself."""
