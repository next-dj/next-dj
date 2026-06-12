"""Django signals emitted by the forms subsystem.

The `action_registered` signal fires after the backend stores an action
target for a name. The sender is the backend class. The keyword
arguments are `action_name`, `uid`, `form_class`, `wizard_class`,
`file_path`, `scope`, and `handler`. Exactly one of `handler`,
`form_class`, or `wizard_class` identifies the registered target,
except the `@action(form_class=...)` path which supplies a handler and
a form factory together. `file_path` is the module the form, wizard, or
handler was declared in and `scope` is `"page"` or `"shared"`. Together
they give receivers a real grouping key under the file-scoped model.

Every dispatch-time signal (`action_dispatched`, `form_validation_failed`,
`wizard_step_submitted`, `wizard_completed`) carries `uid` and `request`.
`uid` is the registry identity of the action, the same value the action
URL and the `data-next-action` markup attribute carry, or `None` when a
custom backend stores no uid in its meta. `request` is the live
`HttpRequest` being dispatched and must not be retained past the
receiver call.

The `action_dispatched` signal fires after a handler runs and the
response has been coerced. The sender is `FormActionDispatch`. The
keyword arguments are `action_name`, `uid`, `request`, `form`,
`url_kwargs`, `duration_ms`, `response_status`, and `dep_cache`.
`form` is the bound form instance after successful validation, or
`None` for handler-only actions registered without a `form_class`.
`url_kwargs` is a copy of the URL kwargs the dispatcher resolved
before invoking the handler. `dep_cache` is a snapshot of the
dispatch DI cache so receivers can read named dependencies
(`Depends("name")` values) resolved during this dispatch without
re-running their providers.

The `form_validation_failed` signal fires when the bound form fails
validation during dispatch. The sender is `FormActionDispatch`. The
keyword arguments are `action_name`, `uid`, `request`, `error_count`,
and `field_names`.

The `wizard_step_submitted` signal fires after a FormWizard step
validates during dispatch. The sender is `FormActionDispatch`. The
keyword arguments are `wizard_class`, `step`, `cleaned_data`, `uid`,
and `request`. `cleaned_data` is a copy of the validated cleaned data
for that step.

The `wizard_completed` signal fires after the wizard `done` method runs
for the final step. The sender is `FormActionDispatch`. The keyword
arguments are `wizard_class`, `cleaned_data`, `uid`, and `request`.
`cleaned_data` is the merged mapping passed to `done`.
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
