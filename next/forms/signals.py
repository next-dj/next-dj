"""Django signals emitted by the forms subsystem.

The `action_registered` signal fires after the backend stores a handler
for a name. The sender is the backend class. The keyword arguments are
`action_name`, `uid`, `form_class`, `namespace`, and `handler`.

The `action_dispatched` signal fires after a handler runs and the
response has been coerced. The sender is `FormActionDispatch`. The
keyword arguments are `action_name`, `form`, `url_kwargs`,
`duration_ms`, `response_status`, and `dep_cache`. `form` is the
bound form instance after successful validation, or `None` for
handler-only actions registered without a `form_class`. `url_kwargs`
is a copy of the URL kwargs the dispatcher resolved before invoking
the handler. `dep_cache` is a snapshot of the dispatch DI cache so
receivers can read named dependencies (`Depends("name")` values)
resolved during this dispatch without re-running their providers.

The `form_validation_failed` signal fires when the bound form fails
validation during dispatch. The sender is `FormActionDispatch`. The
keyword arguments are `action_name`, `error_count`, and `field_names`.
"""

from __future__ import annotations

from django.dispatch import Signal


action_registered: Signal = Signal()
action_dispatched: Signal = Signal()
form_validation_failed: Signal = Signal()


__all__ = ["action_dispatched", "action_registered", "form_validation_failed"]
