from typing import Any

from access.policy import POLICY_FIELD
from django import forms as django_forms

from next import component
from next.forms import FormWizard


STEP_LABELS = {"identity": "Identity", "scope": "Scope", "approval": "Approval"}

STATE_BORDERS = {
    "errors": "border border-rose-300 bg-rose-50/40",
    "active": "border border-slate-200 bg-white shadow-sm",
    "saved": "border border-emerald-200 bg-emerald-50/40",
    "pending": "border border-dashed border-slate-200 bg-slate-50/40",
}
STATE_BADGES = {
    "errors": {"text": "errors", "variant": "destructive"},
    "active": {"text": "active", "variant": "default"},
    "saved": {"text": "✓ saved", "variant": "success"},
    "pending": {"text": "pending", "variant": "muted"},
}


@component.context("sections")
def sections(form: django_forms.Form, wizard: FormWizard) -> list[dict[str, Any]]:
    """Describe every wizard step as a section gated on what the wizard stored."""
    active = wizard.current_step()
    completed = set(wizard.completed_steps())
    stored = wizard.get_all_cleaned_data()
    owned_by_step = _fields_by_step(wizard)
    labels = _field_labels(wizard)
    invalid = bool(form.is_bound and form.errors)
    built = []
    for name in wizard.step_names():
        owned = owned_by_step[name]
        is_active = name == active
        state = _state(is_active=is_active, saved=name in completed, invalid=invalid)
        review = is_active and not owned
        fields = [form[n] for n in owned if n in form.fields] if is_active else []
        summarised = _summarised(owned_by_step, name, state, review=review)
        built.append(
            {
                "key": name,
                "label": STEP_LABELS.get(name, name.replace("_", " ").title()),
                "state": state,
                "border": STATE_BORDERS[state],
                "badge": STATE_BADGES[state],
                "review": review,
                "fields": fields,
                "entries": [
                    {"label": labels[n], "value": stored.get(n, "")} for n in summarised
                ],
            }
        )
    return built


def _summarised(
    owned_by_step: dict[str, list[str]], name: str, state: str, *, review: bool
) -> list[str]:
    """Return the fields a section lists as values rather than as inputs."""
    if review:
        return [field for step in owned_by_step.values() for field in step]
    return owned_by_step[name] if state == "saved" else []


def _fields_by_step(wizard: FormWizard) -> dict[str, list[str]]:
    """Map each step to the data fields it owns, minus the shared acknowledgement.

    It rides on every step form but renders once beneath, so no section owns it alone.
    """
    return {
        name: [n for n in _declared(wizard, name) if n != POLICY_FIELD]
        for name in wizard.step_names()
    }


def _field_labels(wizard: FormWizard) -> dict[str, str]:
    """Read each field's label off its step form class rather than restating it."""
    labels: dict[str, str] = {}
    for name in wizard.step_names():
        form_class = wizard.step_form_class(name)
        for field_name in _declared(wizard, name):
            label = form_class.base_fields[field_name].label
            labels[field_name] = str(label or field_name.replace("_", " ").title())
    return labels


def _declared(wizard: FormWizard, step: str) -> list[str]:
    form_class = wizard.step_form_class(step)
    return list(form_class.base_fields) if form_class is not None else []


def _state(*, is_active: bool, saved: bool, invalid: bool) -> str:
    if not is_active:
        return "saved" if saved else "pending"
    return "errors" if invalid else "active"
