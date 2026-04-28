# Audit-trail forms

A three-step access-request workflow whose every dispatch and validation
failure is recorded by **two parallel audit channels**: a custom
`FormActionBackend` that writes synchronously inside `dispatch`, and signal
receivers reacting to `action_dispatched` and `form_validation_failed`.
The admin page interleaves rows from both channels so you can compare them
side by side.

The example focuses on the form-action subsystem of next-dj: a custom
backend wired through `NEXT_FRAMEWORK["DEFAULT_FORM_ACTION_BACKENDS"]`, a
single namespaced `@action` handling all three steps, three composite
components (`progress_bar` and `step_section` inside the form,
`audit_row` shared between the admin and per-request audit pages),
session-backed step state with a `request_id` correlation column on
`AuditEntry`, and full coverage of the `next.testing` `SignalRecorder`
API.

## What you will see

| URL | Description |
|-----|-------------|
| `/` | Landing page. Recent requests link to their per-request audit. |
| `/request/applicant/` | Step 1 — full name, email, team. Saved sections show "✓ saved" pills. |
| `/request/justification/` | Step 2 — project slug, free-form reason, expiry days. |
| `/request/review/` | Step 3 — read-only confirmation summary. |
| `/request/<id>/audit/` | Per-request audit trail, opened on submit with a "✅ Submitted" banner. |
| `/admin/audit/` | Global audit log. Filter by `kind` via `?kind=…`. Backend rows link to their per-request page. |

The user flow:

```
/  →  /request/applicant/  →  /request/justification/  →  /request/review/  →  submit
                                                                                  ↓
                                                         /request/<new id>/audit/?just=1
```

## How to run

```bash
cd examples/audit-forms
uv run python manage.py migrate
uv run python manage.py runserver     # http://127.0.0.1:8000/
uv run pytest                         # 35 tests
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

The form declares `get_initial(cls, request, step)` as a classmethod.
You never call it directly — the framework calls it automatically with
`step` resolved from the page URL kwarg through the dependency
resolver, and uses the returned dict to seed the form on every GET.
Combined with the session merge in the handler, that gives you
"saved data on every step page" without writing any view code.

### 5. Two composite components, two patterns

The example ships three composite components that demonstrate two
different ways the framework lets a component contribute logic:

- **`progress_bar/` — synthesised state from session truth.** Lives at
  `views/request/[step]/_blocks/progress_bar/`. It does NOT consume a
  pre-built `progress_steps` from the page; it builds the step list
  itself by inspecting `request.session["access_request"]` and the
  current URL kwarg. A step is `current` when it matches the URL,
  `saved` when every field it owns is in the session draft, otherwise
  `pending`. Page-level context shrinks to just `current_step` and
  `draft` — all step knowledge lives in the component.

- **`step_section/` — Python `render()` gating on form state.** Lives
  next to `progress_bar`. Has only a `component.py` (no `.djx`); the
  `render()` function takes `form`, `request`, `step`, and
  `current_step` via DI, then assembles HTML through inline
  `django.template.Template` instances. It owns the section chrome:
  red border on validation errors, "✓ saved" pill plus a compact
  value summary when the step is past, slate placeholder for steps
  not yet visited. The page template just calls
  `{% component "step_section" step="applicant" %}` three times.

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
form handler stores `request.session["access_request_just_created"]`
right after `AccessRequest.objects.create(...)`, and
`AuditedFormActionBackend.dispatch` pops that key after `super()`
returns. Signal-channel rows stay unlinked by design — that is a
teaching point in itself: the signal channel sees only what the A3
payload ships, and `AccessRequest.id` is not in that payload.

### 7. Session state, not hidden form fields

Each step posts the visible fields (plus a hidden `step`). The handler
merges them into `request.session["access_request"]` and redirects. The
form's `get_initial` reads back the same session dict, so on `GET` of
step 2 you can see "Computing" already filled into the team summary —
that is what `tests/test_e2e.py::TestSessionResume` asserts.

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

## Tests

[`tests/test_e2e.py`](tests/test_e2e.py) and
[`tests/test_unit.py`](tests/test_unit.py) hold 35 tests across the
following groups:

- `TestFullSubmission` — three-step happy path; asserts the
  `AccessRequest` row plus the expected backend / signal channel
  counts and `SignalRecorder` events.
- `TestValidationFailure` — empty email on step 1; asserts no
  `AccessRequest` and one signal-source `validation_failed` row with
  `error_count >= 1` and `"email" in field_names`.
- `TestAdminAuditPage` — DOM and DB assertions on the global audit
  table and the `?kind=` filter.
- `TestRequestCorrelation` — backend `dispatched` row links to the
  freshly-created `AccessRequest`; signal rows do not.
- `TestPerRequestAuditPage` — `/request/<id>/audit/` shows only that
  request's rows; unknown id 404s; `?just=1` toggles the success
  banner.
- `TestSuccessRedirect` — final step redirects to
  `/request/<id>/audit/?just=1` with the correct id.
- `TestStepSection` — composite renders `data-state="active"` for
  the current step, `data-state="saved"` plus `data-saved-badge` for
  completed steps.
- `TestNamespacedAction`, `TestSessionResume`, `TestUnknownUid` —
  the existing namespace, session-resume, and unknown-UID assertions
  from the original test pass.
- `TestProgressBarSteps`, `TestStepFallbacks`,
  `TestRequestStepFormDerive`, `TestModelStr`, `TestAuditRowHelpers`,
  `TestLandingPage` — unit-level coverage of derived state, form
  fallbacks, and component helpers.

Coverage is 100% across the `access` package
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
