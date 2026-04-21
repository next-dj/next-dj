"""Django signals emitted by the forms subsystem."""

from __future__ import annotations

from django.dispatch import Signal


action_registered: Signal = Signal()
action_dispatched: Signal = Signal()
form_validation_failed: Signal = Signal()


__all__ = ["action_dispatched", "action_registered", "form_validation_failed"]
