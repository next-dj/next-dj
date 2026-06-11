# Audit-trail forms

A three-step access-request workflow whose every dispatch and validation
failure is recorded by **two parallel audit channels**: a custom
`FormActionBackend` that writes synchronously inside `dispatch`, and signal
receivers reacting to `action_dispatched` and `form_validation_failed`.
The admin page interleaves rows from both channels so you can compare them
side by side.

The example focuses on the form-action subsystem of next-dj: a custom
backend wired through `NEXT_FRAMEWORK["FORM_ACTION_BACKENDS"]`, a
declarative `FormWizard` that routes all three steps from one class, three
composite components (`progress_bar` and `step_section` inside the form,
`audit_row` shared between the admin and per-request audit pages),
session-backed step state with a `request_id` correlation column on
`AuditEntry`, and full coverage of the `next.testing` `SignalRecorder`
API.

## What you will see

| URL | Description |
|-----|-------------|
| `/` | Landing page. Recent requests link to their per-request audit. |
| `/request/identity/` | Step 1 — full name, email, team. Saved sections show "✓ saved" pills. |
| `/request/scope/` | Step 2 — project slug, free-form reason, expiry days. |
| `/request/approval/` | Step 3 — read-only confirmation summary. |
| `/request/<id>/audit/` | Per-request audit trail, opened on submit with a "✅ Submitted" banner. |
| `/admin/audit/` | Global audit log. Filter by `kind` via `?kind=…`. Backend rows link to their per-request page. |

The user flow:

```
/  →  /request/identity/  →  /request/scope/  →  /request/approval/  →  submit
                                                                          ↓
                                                  /request/<new id>/audit/?just=1
```

## How to run

```bash
cd examples/audit-forms
uv run python manage.py migrate
uv run python manage.py runserver     # http://127.0.0.1:8000/
uv run pytest
```

Tailwind loads via the Play CDN in
[`portal/layout.djx`](portal/layout.djx). No Node, no build step. The
wizard threads step data across requests through the configured
`FORM_WIZARD_BACKEND`, which defaults to the Django cache and is
namespaced per session, so keep `SessionMiddleware` in `MIDDLEWARE` (it
is by default in [`config/settings.py`](config/settings.py)).

## Walking the code

### 1. Two audit channels for the same event

`AuditEntry.source` distinguishes them.

- `source="backend"` — written by `AuditedFormActionBackend.dispatch` in
  [`access/backends.py`](access/backends.py). It runs synchronously inside
  the dispatch path so it has direct access to `request.POST` and the
  resolved `HttpResponse`. It writes two rows per dispatch:
  `request_started` (with the captured POST payload) and `dispatched`
  (with `response_status` and the redirect target).
- `source="signal"` — written by the receivers in
  [`access/receivers.py`](access/receivers.py). They subscribe to
  `next.forms.signals.action_dispatched` and `form_validation_failed`,
  using only the kwargs the framework ships in those signals. They never
  see the raw request, which is the whole point: the signal channel is
  decoupled from the backend class.

The two channels intentionally overlap on `kind="dispatched"` so the
admin page can show them side by side. Pick whichever fits your project —
or run both, like this example does.

> **PII caveat.** `_safe_form_payload` strips framework-internal fields
> (`csrfmiddlewaretoken`, `_next_form_page`, `_next_form_origin`, and the
> `_url_param_*` fields) but
> stores every other POST value verbatim, including emails and free-text
> reasons. If you adopt this pattern in production, extend
> `_RESERVED_FORM_KEYS` with any password / secret / personal-data field
> name your forms collect, or hash sensitive values before persisting.
> Multi-value fields (checkbox groups, multi-selects) are preserved via
> `request.POST.lists()`.

### 2. Wiring a custom backend through settings

```python
# config/settings.py
NEXT_FRAMEWORK = {
    ...
    "FORM_ACTION_BACKENDS": [
        {"BACKEND": "access.backends.AuditedFormActionBackend"},
    ],
}
```

The framework loads each entry lazily on first access via
`next.forms.FormActionFactory`. `AuditedFormActionBackend` subclasses
`RegistryFormActionBackend`, so all `@action` registrations are still
honoured — the override only wraps `dispatch` to add the audit rows.

```python
# access/backends.py
class AuditedFormActionBackend(RegistryFormActionBackend):
    def dispatch(self, request, uid):
        AuditEntry.objects.create(...)            # request_started
        response = super().dispatch(request, uid)
        AuditEntry.objects.create(...)            # dispatched
        return response
```

If the dotted path fails to import, the `next.E044` system check fires at
`manage.py check`. If the resolved class is not a `FormActionBackend`,
`next.E045` does. Both checks live in
[`next/forms/checks.py`](../../next/forms/checks.py).

### 3. One declarative `FormWizard` for three steps

```python
# access/views/request/[step]/page.py
class AccessRequestWizard(next.forms.FormWizard):
    class Meta:
        steps = [
            ("identity", IdentityStep),
            ("scope", ScopeStep),
            ("approval", ApprovalStep),
        ]
        url_param = "step"

    def done(self, request, cleaned_data):
        access_request = AccessRequest.objects.create(**cleaned_data)
        request.session["access_request_just_created"] = access_request.pk
        return HttpResponseRedirect(f"/request/{access_request.pk}/audit/?just=1")
```

The class declares itself as one action through `__init_subclass__`, so
the auto-name `access_request_wizard` resolves with
`resolve_action_url("access_request_wizard")`. A namespaced name does
not — see `tests/test_e2e.py::TestNamespacedAction`. Adding a step is one
edit to `Meta.steps`.

### 4. Three ordinary forms, one per step

Each step is a plain `next.forms.ModelForm` (or `Form`):

- `IdentityStep` — a `ModelForm` on `["full_name", "email", "team"]`.
- `ScopeStep` — a `ModelForm` on `["project_slug", "reason", "expires_in_days"]`.
- `ApprovalStep` — a fieldless `Form` that only confirms the merged request.

The wizard binds the current step's form to the POST, validates only
that step's fields, and saves the cleaned data through the wizard
backend. On a non-final step it 302-redirects to the next step's URL, computed by
swapping the `[step]` segment of the origin path. On the final step it
calls `done(request, cleaned_data)` with the merged dict of every step,
so `AccessRequest.objects.create(**cleaned_data)` builds the row once.
No per-step `.save()`, no hidden id fields, no hand-written routing.

### 5. Two composite components, two patterns

The example ships three composite components that demonstrate two
different ways the framework lets a component contribute logic:

- **`progress_bar/` — synthesised state from wizard truth.** Lives at
  `views/request/[step]/_blocks/progress_bar/`. Its `@component.context`
  functions take the `wizard` instance (pushed into the template context
  by the `{% form %}` tag) and read `current_step()`, `step_names()`, and
  `completed_steps()`. A step is `current` when it is the active step,
  `saved` when it has stored data, otherwise `pending`. No page-level
  step context is needed — all step knowledge lives in the wizard.

- **`step_section/` — Python `render()` gating on wizard state.** Lives
  next to `progress_bar`. Has only a `component.py` (no `.djx`); the
  `render()` function takes `form` and `wizard` via DI, then assembles
  HTML through inline `django.template.Template` instances. It owns the
  section chrome: red border on validation errors, "✓ saved" pill plus a
  compact value summary when a step is past, slate placeholder for steps
  not yet visited. The page template calls `{% component "step_section" %}`
  once and the component renders every step.

- **`audit_row/` — `@component.context` deriving display data.** Lives
  at `views/_blocks/audit_row/` (one scope above the admin and
  per-request pages so both can use it). Takes an `AuditEntry` from
  the parent loop and exposes `kind_class`, `source_class`,
  `summary`, `payload_keys`, `request_link`, and `data_attrs`. The
  template stays markup-only.

### 6. Per-request audit trail (`AuditEntry.request` FK)

The audit log can be read globally at `/admin/audit/` or per-request at
`/request/<id>/audit/`. The router walks the file tree and emits both
patterns from one app — `views/request/[step]/page.py` becomes
`request/<str:step>/`, `views/request/[int:id]/audit/page.py` becomes
`request/<int:id>/audit/`. Django's URL resolver picks the int variant
first, so `/request/5/audit/` reaches the per-request page even though
`5` would also be a valid `<str:step>`.

The correlation column on `AuditEntry.request` is **only** populated by
the backend channel, on the **dispatched** row of the final step. The
wizard's `done` stores `request.session["access_request_just_created"]`
right after `AccessRequest.objects.create(...)`, and
`AuditedFormActionBackend.dispatch` pops that key after `super()`
returns. Signal-channel rows stay unlinked by design — that is a
teaching point in itself: the signal channel sees only the kwargs the
signal ships, and `AccessRequest.id` is not among them.

### 7. Wizard backend, not hidden form fields

Each step posts only its visible fields plus the framework's hidden
`_url_param_step`, `_next_form_origin`, and `_next_form_page` (all emitted
by the `{% form %}` tag). The wizard saves the cleaned data through the
configured `FORM_WIZARD_BACKEND` (the Django cache by default),
so on `GET` of step 2 you can see "Computing" already filled into the
team summary — that is what `tests/test_e2e.py::TestSessionResume`
asserts. Point `FORM_WIZARD_BACKEND` at a Redis or custom backend
without touching any view code.

### 8. Admin filter by GET query

`/admin/audit/?kind=validation_failed` narrows the table to one kind
through a plain GET form. The `@context("active_kind")` function reads
`request.GET` and the template uses it to mark the matching `<option>`
as `selected`. No JavaScript, no AJAX.

### 9. Comparing the two audit channels

| | Backend channel | Signal channel |
|---|---|---|
| Where written | inside `AuditedFormActionBackend.dispatch` | `@receiver(action_dispatched / form_validation_failed)` |
| Sees raw POST? | yes | no — only the signal kwargs |
| Sees response status? | yes | yes (via signal kwarg) |
| Correlated to `AccessRequest`? | yes (last step only) | no |
| Coupled to backend class? | yes — only fires when this backend dispatches | no — fires whatever backend is configured |
| When to pick | compliance, full request payloads, transactional rollback | metrics, side effects on action lifecycle, decoupled from backend swap |

The example runs both because it is a *demonstration*. In production,
pick the channel that matches your need: backend if you want raw
inputs and atomicity with the form's database write, signal if you
want decoupling and minimal coupling to the backend implementation.

## Further reading

- [`next/forms/wizard.py`](../../next/forms/wizard.py) — the declarative
  `FormWizard` base class, the `FormWizardBackend` contract, and the
  default cache-backed `CacheFormWizardBackend` this example builds on.
- [`next/forms/manager.py`](../../next/forms/manager.py) — the lazy,
  settings-driven `FormActionManager` used by every example.
- [`next/forms/backends.py`](../../next/forms/backends.py) — the
  `FormActionBackend` ABC and `RegistryFormActionBackend` superclass.
- [`next/forms/dispatch.py`](../../next/forms/dispatch.py) — where
  `action_dispatched`, `form_validation_failed`, `wizard_step_submitted`,
  and `wizard_completed` are sent.
- [`next/forms/checks.py`](../../next/forms/checks.py) — `next.E041`
  (duplicate handlers), `next.E044` (bad backend config), `next.E045`
  (wrong backend type).
- [`next/testing/signals.py`](../../next/testing/signals.py) —
  `SignalRecorder` and `capture_signals` helpers used in the tests.
- [`docs/content/guide/testing.rst`](../../docs/content/guide/testing.rst)
  — canonical conftest scaffold mirrored in this example.
