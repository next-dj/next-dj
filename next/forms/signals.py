"""Django signals emitted by the forms subsystem.

The `action_registered` signal fires after the backend stores a handler
for a name. The sender is the backend class. The keyword arguments are
`action_name`, `uid`, `form_class`, and `namespace`.

The `action_dispatched` signal fires after a handler runs and the
response has been coerced. The sender is `_FormActionDispatch`. The
keyword arguments are `action_name`, `duration_ms`, and
`response_status`.

The `form_validation_failed` signal fires when the bound form fails
validation during dispatch. The sender is `_FormActionDispatch`. The
keyword arguments are `action_name`, `error_count`, and `field_names`.
"""

from __future__ import annotations

from django.dispatch import Signal


action_registered: Signal = Signal()
action_dispatched: Signal = Signal()
form_validation_failed: Signal = Signal()


__all__ = ["action_dispatched", "action_registered", "form_validation_failed"]
