from dataclasses import dataclass

from django import forms as django_forms
from django.template import Context, Template
from django.utils.html import escape

from next.forms import FormWizard


_FIELDS_TEMPLATE = Template(
    """
    {% for field in rendered_fields %}
      <label class="block">
        <span class="block text-sm font-medium text-slate-700">{{ field.label }}</span>
        {{ field.widget|safe }}
        {% if field.errors %}
          <span class="mt-1 block text-xs text-rose-600">{{ field.errors|first }}</span>
        {% endif %}
      </label>
    {% endfor %}
    """,
)

_REVIEW_TEMPLATE = Template(
    """
    <div class="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm">
      <h2 class="text-sm font-semibold text-slate-700">Confirm and submit</h2>
      <dl class="mt-3 grid grid-cols-2 gap-y-2 text-sm">
        <dt class="text-slate-500">Full name</dt><dd>{{ draft.full_name }}</dd>
        <dt class="text-slate-500">Email</dt><dd>{{ draft.email }}</dd>
        <dt class="text-slate-500">Team</dt><dd>{{ draft.team }}</dd>
        <dt class="text-slate-500">Project</dt><dd>{{ draft.project_slug }}</dd>
        <dt class="text-slate-500">Days</dt><dd>{{ draft.expires_in_days }}</dd>
        <dt class="text-slate-500">Reason</dt>
        <dd class="whitespace-pre-line">{{ draft.reason }}</dd>
      </dl>
    </div>
    """,
)

_SAVED_SUMMARY_TEMPLATE = Template(
    """
    <dl class="grid grid-cols-3 gap-x-3 gap-y-1 text-xs text-slate-600">
      {% for entry in entries %}
        <dt class="text-slate-400">{{ entry.label }}</dt>
        <dd class="col-span-2 truncate">{{ entry.value }}</dd>
      {% endfor %}
    </dl>
    """,
)

_FIELD_LABELS = {
    "full_name": "Full name",
    "email": "Email",
    "team": "Team",
    "project_slug": "Project slug",
    "reason": "Reason",
    "expires_in_days": "Expires in days",
}

_STEP_LABELS = {
    "identity": "Identity",
    "scope": "Scope",
    "approval": "Approval",
}


@dataclass(frozen=True, slots=True)
class _Section:
    """One wizard step's render inputs, pairing the owner step with stored truth."""

    name: str
    active: str
    owned: list[str]
    stored: dict[str, object]
    saved: bool


def render(
    form: django_forms.Form,
    wizard: FormWizard,
) -> str:
    """Render every wizard step as a section gated on its stored state.

    The active step shows its bound fields (or the review summary on the
    final step). Completed steps show a saved summary with a badge.
    Pending steps render an empty placeholder.
    """
    active = wizard.current_step()
    completed = set(wizard.completed_steps())
    stored = wizard.get_all_cleaned_data()
    fields_by_step = _fields_by_step(wizard)
    chunks = []
    for name in wizard.step_names():
        section = _Section(
            name=name,
            active=active,
            owned=fields_by_step.get(name, []),
            stored=stored,
            saved=name in completed,
        )
        has_errors = name == active and _has_errors(form)
        state = _state(name, active, saved=section.saved, has_errors=has_errors)
        chunks.append(_render_section(form, section, state))
    return "".join(chunks)


def _fields_by_step(wizard: FormWizard) -> dict[str, list[str]]:
    by_step: dict[str, list[str]] = {}
    for name in wizard.step_names():
        form_class = wizard.step_form_class(name)
        by_step[name] = list(form_class.base_fields) if form_class is not None else []
    return by_step


def _render_section(form: django_forms.Form, section: _Section, state: str) -> str:
    label = _STEP_LABELS.get(section.name, section.name.replace("_", " ").title())
    body = _body_for(form, section)
    return (
        f'<section data-step-section="{escape(section.name)}" data-state="{state}" '
        f'class="{_border_class(state)} rounded-lg p-4 space-y-3">'
        f'<header class="flex items-center justify-between text-xs uppercase '
        f'tracking-wide text-slate-500"><span>{escape(label)}</span>'
        f"{_badge(state)}</header>"
        f"{body}</section>"
    )


def _body_for(form: django_forms.Form, section: _Section) -> str:
    if section.name == section.active and not section.owned:
        return _REVIEW_TEMPLATE.render(Context({"draft": section.stored}))
    if section.name == section.active:
        rendered = [
            {
                "label": _FIELD_LABELS.get(field, field.replace("_", " ").title()),
                "widget": str(form[field]),
                "errors": form[field].errors,
            }
            for field in section.owned
            if field in form.fields
        ]
        return _FIELDS_TEMPLATE.render(Context({"rendered_fields": rendered}))
    if section.saved:
        return _saved_summary(section.owned, section.stored)
    return ""


def _saved_summary(owned: list[str], stored: dict[str, object]) -> str:
    entries = [
        {
            "label": _FIELD_LABELS.get(field, field.replace("_", " ").title()),
            "value": _display(stored.get(field, "")),
        }
        for field in owned
    ]
    return _SAVED_SUMMARY_TEMPLATE.render(Context({"entries": entries}))


_DISPLAY_TRUNCATE_AT = 60
_DISPLAY_TRUNCATE_TO = 57


def _display(value: object) -> str:
    text = str(value)
    if len(text) > _DISPLAY_TRUNCATE_AT:
        return text[:_DISPLAY_TRUNCATE_TO] + "..."
    return text


def _has_errors(form: django_forms.Form) -> bool:
    return bool(getattr(form, "is_bound", False) and form.errors)


def _state(name: str, active: str, *, saved: bool, has_errors: bool) -> str:
    if has_errors:
        return "errors"
    if name == active:
        return "active"
    if saved:
        return "saved"
    return "pending"


def _border_class(state: str) -> str:
    if state == "errors":
        return "border border-rose-300 bg-rose-50/40"
    if state == "active":
        return "border border-slate-200 bg-white shadow-sm"
    if state == "saved":
        return "border border-emerald-200 bg-emerald-50/40"
    return "border border-dashed border-slate-200 bg-slate-50/40"


def _badge(state: str) -> str:
    if state == "saved":
        return (
            '<span data-saved-badge class="rounded-full bg-emerald-100 px-2 '
            'py-0.5 text-[10px] font-semibold text-emerald-800">✓ saved</span>'
        )
    if state == "active":
        return (
            '<span class="rounded-full bg-slate-900 px-2 py-0.5 text-[10px] '
            'font-semibold text-white">active</span>'
        )
    if state == "errors":
        return (
            '<span class="rounded-full bg-rose-100 px-2 py-0.5 text-[10px] '
            'font-semibold text-rose-800">errors</span>'
        )
    return (
        '<span class="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] '
        'font-semibold text-slate-500">pending</span>'
    )
