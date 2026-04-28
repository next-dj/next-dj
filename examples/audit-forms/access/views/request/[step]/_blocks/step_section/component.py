from access.steps import STEP_FIELDS, STEP_LABEL, normalise_step
from django import forms as django_forms
from django.http import HttpRequest
from django.template import Context, Template
from django.utils.html import escape


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


def render(
    form: django_forms.Form,
    request: HttpRequest,
    step: str,
    current_step: str = "applicant",
) -> str:
    """Render one section of the multi-step request form.

    The composite owns the section's chrome — borders, error highlight,
    "saved" badge, the review summary — based on the relationship
    between ``step`` (the section's owner) and ``current_step`` (the
    active page step), plus whatever the bound form reports under
    ``form.errors``. Every step is rendered on every step page so the
    user sees previously-captured context alongside the active fields.
    """
    target = normalise_step(step)
    active = normalise_step(current_step)
    saved = _is_saved(request, target)
    has_errors = target == active and _has_errors(form, STEP_FIELDS[target])
    state = _state(target, active, saved=saved, has_errors=has_errors)

    body = _body_for(form, request, target, active)
    badge = _badge(state)
    border = _border_class(state)

    return (
        f'<section data-step-section="{escape(target)}" data-state="{state}" '
        f'class="{border} rounded-lg p-4 space-y-3">'
        f'<header class="flex items-center justify-between text-xs uppercase '
        f'tracking-wide text-slate-500"><span>{escape(STEP_LABEL[target])}</span>'
        f"{badge}</header>"
        f"{body}</section>"
    )


def _body_for(
    form: django_forms.Form,
    request: HttpRequest,
    target: str,
    active: str,
) -> str:
    if target == active and target == "review":
        draft = dict(request.session.get("access_request", {}))
        return _REVIEW_TEMPLATE.render(Context({"draft": draft}))
    if target == active:
        rendered = [
            {
                "label": _FIELD_LABELS.get(name, name.replace("_", " ").title()),
                "widget": str(form[name]),
                "errors": form[name].errors,
            }
            for name in STEP_FIELDS[target]
            if name in form.fields
        ]
        return _FIELDS_TEMPLATE.render(Context({"rendered_fields": rendered}))
    if _is_saved(request, target):
        return _saved_summary(request, target)
    return ""


def _saved_summary(request: HttpRequest, target: str) -> str:
    draft = request.session.get("access_request", {})
    entries = [
        {
            "label": _FIELD_LABELS.get(name, name.replace("_", " ").title()),
            "value": _display(draft.get(name, "")),
        }
        for name in STEP_FIELDS[target]
    ]
    return _SAVED_SUMMARY_TEMPLATE.render(Context({"entries": entries}))


_DISPLAY_TRUNCATE_AT = 60
_DISPLAY_TRUNCATE_TO = 57


def _display(value: object) -> str:
    text = str(value)
    if len(text) > _DISPLAY_TRUNCATE_AT:
        return text[:_DISPLAY_TRUNCATE_TO] + "..."
    return text


def _is_saved(request: HttpRequest, step: str) -> bool:
    draft = request.session.get("access_request", {})
    fields = STEP_FIELDS[step]
    return bool(fields) and all(field in draft for field in fields)


def _has_errors(form: django_forms.Form, field_names: list[str]) -> bool:
    return any(form[name].errors for name in field_names if name in form.fields)


def _state(target: str, active: str, *, saved: bool, has_errors: bool) -> str:
    if has_errors:
        return "errors"
    if target == active:
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
