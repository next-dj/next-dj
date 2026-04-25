# Audit-trail forms

A three-step access-request workflow whose every dispatch and validation
failure is recorded by **two parallel audit channels**: a custom
`FormActionBackend` that writes synchronously inside `dispatch`, and signal
receivers reacting to `action_dispatched` and `form_validation_failed`.
The admin page interleaves rows from both channels so you can compare them
side by side.

The example focuses on the form-action subsystem of next-dj: a custom
backend wired through `NEXT_FRAMEWORK["DEFAULT_FORM_ACTION_BACKENDS"]`, a
single namespaced `@action` handling all three steps, two composite
components (`progress_bar` inside the form, `audit_row` inside the admin
table), session-backed step state, and full coverage of the
`next.testing` `SignalRecorder` API.

## What you will see

| URL | Description |
|-----|-------------|
| `/` | Landing page with two CTAs and snapshots of the latest five requests and audit rows. |
| `/request/applicant/` | Step 1 of the request form — full name, email, team. |
| `/request/justification/` | Step 2 — project slug, free-form reason, expiry days. |
| `/request/review/` | Step 3 — read-only summary, "Submit request" button. |
| `/admin/audit/` | Audit log table. Filter by `kind` via `?kind=…`. Backend rows and signal rows live next to each other. |

## How to run

```bash
cd examples/audit-forms
uv run python manage.py migrate
uv run python manage.py runserver     # http://127.0.0.1:8000/
uv run pytest                         # 9 tests
```

Tailwind loads via the Play CDN in
[`access/views/layout.djx`](access/views/layout.djx). No Node, no build
step. The example uses Django sessions to thread step data, so make sure
`SESSION` middleware stays in `MIDDLEWARE` (it is by default in
[`config/settings.py`](config/settings.py)).

## Project tour

```
examples/audit-forms/
├── config/
│   ├── settings.py             # PAGES_DIR="views", COMPONENTS_DIR="_blocks",
│   │                           # DEFAULT_FORM_ACTION_BACKENDS=[AuditedFormActionBackend]
│   └── urls.py                 # Only include('next.urls') — file router owns every route
└── access/
    ├── apps.py                 # AppConfig.ready() imports backends + receivers
    ├── models.py               # AccessRequest, AuditEntry (with source + kind discriminators)
    ├── backends.py             # AuditedFormActionBackend — writes "backend"-source rows
    ├── receivers.py            # action_dispatched / form_validation_failed → "signal"-source rows
    └── views/                  # ← PAGES_DIR
        ├── layout.djx          # Root chrome — Tailwind, header, top nav
        ├── page.py             # @context("recent_requests"), @context("recent_audit")
        ├── template.djx        # Landing UI
        ├── request/
        │   └── [step]/
        │       ├── page.py     # RequestStepForm + @action("request_step", namespace="access")
        │       ├── template.djx
        │       └── _blocks/    # ← scoped composite components
        │           └── progress_bar/
        │               ├── component.py   # @component.context("step_label", "completed_count")
        │               └── component.djx
        └── admin/
            └── audit/
                ├── page.py     # @context("entries", "active_kind", "totals")
                ├── template.djx
                └── _blocks/
                    └── audit_row/
                        ├── component.py   # @component.context for row-derived view-data
                        └── component.djx
```

The two composite components live next to the routes they are scoped to.
The `_blocks/` directory name matches `COMPONENTS_DIR="_blocks"` in
[`config/settings.py`](config/settings.py).

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
> (CSRF token, `_next_form_uid`, `_next_form_page`, `_url_param_*`) but
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
    "DEFAULT_FORM_ACTION_BACKENDS": [
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

### 3. One namespaced action for three steps

```python
# access/views/request/[step]/page.py
@action("request_step", namespace="access", form_class=RequestStepForm)
def request_step(form: RequestStepForm, request: HttpRequest) -> ...
```

The action key in the registry becomes `"access:request_step"`. Two apps
can register `request_step` under different namespaces without colliding,
and `resolve_action_url("access:request_step")` finds it. The bare name
does not — see `tests/test_e2e.py::TestNamespacedAction`.

### 4. A single `Form` whose fields change per step

`RequestStepForm.__init__` reads the bound or initial `step` value, then
attaches the field set for that step from `STEP_FIELD_BUILDERS`. Step 1
collects identity, step 2 collects justification, step 3 has no extra
fields and only renders a confirmation. The handler reads
`form.cleaned_data["step"]` to decide whether to redirect to the next
step or commit the `AccessRequest`.

This pattern is small enough to read in one screen and demonstrates the
DI-driven `get_initial(cls, request, step)` hook — the page URL kwarg
`step` is resolved through the framework's dependency resolver, so the
form repopulates from `request.session["access_request"]` on every GET.

### 5. Composite components scoped to their section

Both composite components live in `_blocks/` next to their parent route,
not at the top of `views/`. The component scanner only makes them visible
inside that subtree, so neither leaks into unrelated pages.

`progress_bar/` reads `progress_steps` from the page context (one of the
`@context` functions in `page.py`) and adds derived view-data through
`@component.context` — `step_label`, `completed_count`. Its template
renders the stepper using both inputs.

`audit_row/` receives an `entry` from the admin template's
`{% for entry in entries %}` loop and adds `kind_class`, `source_class`,
`summary`, `payload_keys`, and `data_attrs` so the template stays
markup-only.

### 6. Session state, not hidden form fields

Each step posts the visible fields (plus a hidden `step`). The handler
merges them into `request.session["access_request"]` and redirects. The
form's `get_initial` reads back the same session dict, so on `GET` of
step 2 you can see "Computing" already filled into the team summary —
that is what `tests/test_e2e.py::TestSessionResume` asserts.

### 7. Admin filter by GET query

`/admin/audit/?kind=validation_failed` narrows the table to one kind
through a plain GET form. The `@context("active_kind")` function reads
`request.GET` and the template uses it to mark the matching `<option>`
as `selected`. No JavaScript, no AJAX.

## Tests

[`tests/test_e2e.py`](tests/test_e2e.py) groups nine tests across five
classes:

- `TestFullSubmission` — three-step happy path. Asserts a single
  `AccessRequest`, six backend rows (3× `request_started` + 3×
  `dispatched`), three signal rows, and that `SignalRecorder` captures
  three `action_dispatched` events with non-zero `duration_ms`.
- `TestValidationFailure` — empty email on step 1. Asserts no
  `AccessRequest`, one signal-source `validation_failed` row with
  `error_count >= 1` and `"email" in field_names`, and one
  `form_validation_failed` event recorded by `SignalRecorder`.
- `TestAdminAuditPage` — both `data-source="backend"` and
  `data-source="signal"` `<tr>` elements appear after one valid and one
  invalid post. The `?kind=` filter narrows the rendered HTML.
- `TestNamespacedAction` — `resolve_action_url("access:request_step")`
  succeeds, the bare name raises `KeyError`.
- `TestSessionResume` — POST step 1, then GET `/request/justification/`,
  and assert "Computing" appears in the rendered HTML.

Coverage is 95% across the `access` package
(`uv run pytest --cov=access`).

## Forward-compat

- **Partial rerender**. The handler returns `HttpResponseRedirect` on
  every step. The day the framework grows `form.partial_response(...)`,
  switching is a one-line edit per step — no template restructure.
- **Suspense / async backends**. `AuditedFormActionBackend.dispatch` is
  the only async-eligible spot. Subclassing it again with an
  async-aware variant or wrapping the receivers in a queue does not
  change the example's URL surface or test assertions.
- **Native parent context**. `@context` already runs at the page level
  and the admin filter context. When the framework adds native
  `inherit_context=True` on `@context`, the admin page can move common
  context up without touching the template.
- **Pluggable serializers**. The audit rows store JSON via Django's
  `JSONField`. Switching to msgspec or pydantic for `AuditEntry.payload`
  is a model-only change.

## Further reading

- [`next/forms/manager.py`](../../next/forms/manager.py) — the lazy,
  settings-driven `FormActionManager` used by every example.
- [`next/forms/backends.py`](../../next/forms/backends.py) — the
  `FormActionBackend` ABC and `RegistryFormActionBackend` superclass.
- [`next/forms/dispatch.py`](../../next/forms/dispatch.py) — where
  `action_dispatched` and `form_validation_failed` are sent.
- [`next/forms/checks.py`](../../next/forms/checks.py) — `next.E041`
  (duplicate handlers), `next.E044` (bad backend config), `next.E045`
  (wrong backend type).
- [`next/testing/signals.py`](../../next/testing/signals.py) —
  `SignalRecorder` and `capture_signals` helpers used in the tests.
- [`docs/content/guide/testing.rst`](../../docs/content/guide/testing.rst)
  — canonical conftest scaffold mirrored in this example.
