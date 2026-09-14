"""Django signals emitted by the forms subsystem."""

from django.dispatch import Signal


action_registered: Signal = Signal(use_caching=True)
action_dispatched: Signal = Signal(use_caching=True)
form_validation_failed: Signal = Signal(use_caching=True)
wizard_step_submitted: Signal = Signal(use_caching=True)
wizard_completed: Signal = Signal(use_caching=True)
form_access_denied: Signal = Signal(use_caching=True)


__all__ = [
    "action_dispatched",
    "action_registered",
    "form_access_denied",
    "form_validation_failed",
    "wizard_completed",
    "wizard_step_submitted",
]
