"""Django signals emitted by the forms subsystem.

The `action_registered` signal fires after the backend stores a handler
for a name. The sender is the backend class. The keyword arguments are
`action_name`, `uid`, `form_class`, `file_path`, `scope`, and `handler`.
`file_path` is the module the form or handler was declared in and `scope`
is `"page"` or `"shared"`. Together they give receivers a real grouping
key under the file-scoped model.

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

The `wizard_step_submitted` signal fires after a FormWizard step
validates during dispatch. The sender is `FormActionDispatch`. The
keyword arguments are `wizard_class`, `step`, and `cleaned_data`.
`cleaned_data` is a copy of the validated cleaned data for that step.

The `wizard_completed` signal fires after the wizard `done` method runs
for the final step. The sender is `FormActionDispatch`. The keyword
arguments are `wizard_class` and `cleaned_data`. `cleaned_data` is the
merged mapping passed to `done`.
"""

from __future__ import annotations

from django.dispatch import Signal


action_registered: Signal = Signal()
action_dispatched: Signal = Signal()
form_validation_failed: Signal = Signal()
wizard_step_submitted: Signal = Signal()
wizard_completed: Signal = Signal()


__all__ = [
    "action_dispatched",
    "action_registered",
    "form_validation_failed",
    "wizard_completed",
    "wizard_step_submitted",
]
