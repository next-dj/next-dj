# Audit-trail forms

A three-step access-request workflow whose every dispatch and validation
failure is recorded by **two parallel audit channels**: a custom
`FormActionBackend` that writes synchronously inside `dispatch`, and signal
receivers reacting to `action_dispatched`, `form_validation_failed`, and
`form_access_denied`. The admin page interleaves rows from both channels so
you can compare them side by side.

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
| `/` | Landing page. "Start a new request" opens the wizard in a modal layer and refreshes the recent-requests list when it closes. Recent requests link to their per-request audit. |
| `/request/identity/` | Step 1 — full name, email, team. Saved sections show "✓ saved" pills. |
| `/request/scope/` | Step 2 — project slug, free-form reason, expiry days. |
| `/request/approval/` | Step 3 — read-only confirmation summary. |
| `/request/<id>/audit/` | Per-request audit trail, opened on submit with a "✅ Submitted" banner. |
| `/admin/audit/` | Global audit log. The heavy table is a lazy `audit-table` zone behind a skeleton. Filter by `kind` via `?kind=…` (`access_denied` included). Backend rows link to their per-request page. |

The user flow:

```
/  →  /request/identity/  →  /request/scope/  →  /request/approval/  →  submit
                                                                          ↓
                                                  /request/<new id>/audit/?just=1
```

With the client runtime loaded, the same three steps run inside a modal
layer over the landing page, and the final step closes the modal and
refreshes the recent-requests list in place. Both paths are described in
[the modal-wizard walkthrough](#10-the-flagship-a-modal-wizard-that-refreshes-a-list)
below, and both are exercised by the test suite.

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
`FORM_WIZARD_BACKEND`, which defaults to the session-backed
`SessionFormWizardBackend`, so keep `SessionMiddleware` in `MIDDLEWARE`
(it is by default in [`config/settings.py`](config/settings.py)).

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
  `next.forms.signals.action_dispatched`, `form_validation_failed`, and
  `form_access_denied`, using only the kwargs the framework ships in those
  signals. They never see the raw request, which is the whole point: the
  signal channel is decoupled from the backend class.

The two channels intentionally overlap on `kind="dispatched"` so the
admin page can show them side by side. Pick whichever fits your project —
or run both, like this example does.

> **PII caveat.** `_safe_form_payload` strips control fields
> (`csrfmiddlewaretoken`, `_next_form_origin`, and the
> `policy_acknowledged` gate field) but stores every other POST value
> verbatim, including emails and free-text
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
`next.forms.backends.FormActionFactory`. `AuditedFormActionBackend` subclasses
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

    def done(self, request: HttpRequest, cleaned_data):
        access_request = AccessRequest.objects.create(**cleaned_data)
        request.session["access_request_just_created"] = access_request.pk
        return HttpResponseRedirect(f"/request/{access_request.pk}/audit/?just=1")
```

The class declares itself as one action through `__init_subclass__`, so
the auto-name `access_request_wizard` resolves with
`resolve_action_url("access_request_wizard")`. A namespaced name does
not — see `tests/test_e2e.py::TestNamespacedAction`. Adding a step is one
edit to `Meta.steps`.

### 3a. A dynamic permission gate on the wizard

```python
# access/views/request/[step]/page.py
class AccessRequestWizard(next.forms.FormWizard):
    @classmethod
    def check_permissions(cls, request):
        return request.POST.get("policy_acknowledged") == "on"
```

`check_permissions` is a DI-resolved classmethod the framework runs on
**every step POST, before the step form binds**. It declares only what it
reads — here the `request` and its POST data. Return `None` or `True` to
allow, `False` or `raise PermissionDenied` to deny with a 403, or return
an `HttpResponse` to short-circuit verbatim. A denied step writes no
draft, so the wizard storage stays untouched.

The gate is the retention-policy acknowledgement. The step template
renders a checked `policy_acknowledged` checkbox inside the form (the
`data-policy-notice` block), so a normal submission carries the field and
passes, while a replayed or forged action URL that never rendered the
form omits it and is denied. The acknowledgement field is a control field,
not user data, so `_RESERVED_FORM_KEYS` in `access/backends.py` strips it
from the captured payload. That denial is exactly the kind of event an
audit example should capture.

The framework fires `next.forms.signals.form_access_denied` **only** on a
dynamic-hook denial, never on the static `ActionGuard` fast-path. The
sender is `FormActionDispatch` and the kwargs are `action_name`, `uid`,
`request`, `layer` (`"view"` for `check_permissions`, `"object"` for
`has_object_permission`), and `reason` (`"denied"` when the hook returned
`False`, `"raised"` on `PermissionDenied`, `"response"` on an
`HttpResponse` short-circuit). The receiver in
[`access/receivers.py`](access/receivers.py) records one signal-sourced
`AuditEntry` per denial with `kind="access_denied"`, storing `layer` and
`reason` in the dedicated `access_layer` / `access_reason` columns:

```python
# access/receivers.py
@receiver(form_access_denied)
def _on_form_access_denied(action_name, layer, reason, **_):
    AuditEntry.objects.create(
        action_name=action_name,
        kind=AuditEntry.KIND_ACCESS_DENIED,
        source=AuditEntry.SOURCE_SIGNAL,
        access_layer=layer,
        access_reason=reason,
    )
```

Because the denied step writes no draft and no `dispatched` row, this
`access_denied` row is the only trace that records *why* the request was
refused — the backend channel still leaves a `request_started` row (with
no `reason`) before `super().dispatch` reaches the denying hook, which is
the whole point of the signal channel. Filter the admin log to the denial
with `/admin/audit/?kind=access_denied`.

### 4. Three ordinary forms, one per step

Each step is a bare `django.forms.ModelForm` (or `Form`) — the wizard
owns dispatching, so step forms never register as standalone actions
and need none of the `next.forms` base classes (a step that does
subclass `next.forms` and ends up registered trips the `next.W057`
check):

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
`_next_form_origin` (emitted by the `{% form %}` tag). The dispatcher
resolves that origin URL against the URLconf to recover the typed
`step` kwarg, and `_step_from_origin` in the audit backend does the
same for the audit rows. The wizard saves the cleaned data through the
configured `FORM_WIZARD_BACKEND` (the session-backed
`SessionFormWizardBackend` by default), so on `GET` of step 2 you can
see "Computing" already filled into the team summary — that is what
`tests/test_e2e.py::TestSessionResume` asserts. Point
`FORM_WIZARD_BACKEND` at `CacheFormWizardBackend` when drafts need
their own TTL or a Redis-backed cache, or at a custom backend, without
touching any view code.

### 8. Admin filter by GET query, plus a lazy audit table

`/admin/audit/?kind=validation_failed` narrows the table to one kind
through a plain GET form. The `@context("active_kind")` function reads
`request.GET` and the template uses it to mark the matching `<option>`
as `selected`.

The heavy table is a lazy zone. `views/admin/audit/template.djx` wraps
it in `{% zone "audit-table" lazy="revealed" %}` with a `{% placeholder %}`
branch of `skeleton` bars. On the full page render only the placeholder
renders, and the body — the `<table>` with up to a hundred rows — is
skipped. The body arrives as a morph patch when the zone scrolls into
view, so the table is fetched on demand rather than on first paint.

The expensive query is guarded by `zone_requested`, the idiom that makes
the laziness honest rather than cosmetic:

```python
from next.partial import zone_requested

@context("entries")
def entries(request: HttpRequest) -> list[AuditEntry] | None:
    if not zone_requested(request, "audit-table"):
        return None
    qs = AuditEntry.objects.all()
    requested_kind = request.GET.get("kind", "")
    if requested_kind in _VALID_KIND_FILTERS:
        qs = qs.filter(kind=requested_kind)
    return list(qs[:100])
```

On the full render the provider returns `None` and the query never
runs. On the zone GET — where `X-Next-Zone: audit-table` is set —
`zone_requested` is true and the rows load. The `?kind=` filter still
reads `request.GET`, so it works on the zone request the same way it
worked on the old full page. Without the runtime the placeholder simply
stays, so the critical, always-needed content lives outside the lazy
zone by design.

### 9. Comparing the two audit channels

| | Backend channel | Signal channel |
|---|---|---|
| Where written | inside `AuditedFormActionBackend.dispatch` | `@receiver(action_dispatched / form_validation_failed / form_access_denied)` |
| Sees raw POST? | yes | no — only the signal kwargs |
| Sees response status? | yes | yes (via signal kwarg) |
| Records permission denials? | partially — a denial raises before the `dispatched` row, leaving only the `request_started` row with no reason | yes — `form_access_denied` carries `layer` and `reason` |
| Correlated to `AccessRequest`? | yes (last step only) | no |
| Coupled to backend class? | yes — only fires when this backend dispatches | no — fires whatever backend is configured |
| When to pick | compliance, full request payloads, transactional rollback | metrics, side effects on action lifecycle, decoupled from backend swap |

The example runs both because it is a *demonstration*. In production,
pick the channel that matches your need: backend if you want raw
inputs and atomicity with the form's database write, signal if you
want decoupling and minimal coupling to the backend implementation.

### 10. The flagship: a modal wizard that refreshes a list

The landing page and the wizard wire together into one interaction that
needs no project JavaScript: click "Start a new request", fill the wizard
inside a modal, and watch the recent-requests list refresh under the modal
as it closes. The whole thing is three template attributes, two zones, and
one builder call.

**The opening link and the list zone.** The landing page
([`access/views/template.djx`](access/views/template.djx)) carries one
plain link with two attributes and wraps the list in a named zone:

```django
<a href="{% url 'next:page_request_step' step='identity' %}"
   data-next-layer="access-wizard" data-next-accepted="request-list">
  Start a new request
</a>

{% zone "request-list" %}
  <ul>
    {% for r in recent_requests %}
      <li data-next-key="{{ r.pk }}">…</li>
    {% endfor %}
  </ul>
{% endzone %}
```

`data-next-layer` names the zone the modal hosts, `data-next-accepted`
names the page zone to re-fetch once the modal closes with an accept
result, and `data-next-key` lets the list morph identify rows by primary
key. No selector and no swap mode appear in the markup — the server
authors every operation, the client only names intent.

**The wizard inside a zone.** The step template
([`access/views/request/[step]/template.djx`](access/views/request/%5Bstep%5D/template.djx))
wraps the existing form in the `access-wizard` zone and turns on blur
validation:

```django
{% zone "access-wizard" %}
  {% form "access_request_wizard" validate="blur" zone="access-wizard" %}
    …
  {% endform %}
{% endzone %}
```

The page module did not change for routing or steps. The only Python edit
is the wizard's `done`, which now closes the layer and toasts instead of
returning a bare redirect:

```python
# access/views/request/[step]/page.py
def done(self, request, cleaned_data):
    access_request = AccessRequest.objects.create(**cleaned_data)
    request.session["access_request_just_created"] = access_request.pk
    request.session.modified = True
    return (
        Patches(request)
        .layer_close(result={"id": access_request.pk})
        .toast("Access request submitted", variant="success")
        .response(fallback=f"/request/{access_request.pk}/audit/?just=1")
    )
```

The session key still threads the new request id to the backend audit
row, exactly as before — the builder change does not break the
correlation column.

**With the runtime.** The link opens a native `<dialog>` and creates an
empty `access-wizard` container before the request, then GETs the step
page for that zone alone. Each step submits inside the modal: an invalid
step morphs only the `access-wizard` zone and the modal stays open, a
valid non-final step morphs the zone to the next step with no redirect, and
the final step's `done` returns `layer.close` plus a toast. The runtime
closes the modal and, because the opening link named `data-next-accepted`,
re-GETs the `request-list` zone of the landing page with its own cookies,
so the list authorizes and renders in its own view before morphing under
the now-closed modal.

**Without the runtime.** Every attribute degrades to a plain link or
form. The link navigates to the full `/request/identity/` page, each step
posts and `302`-redirects to the next step's page, and the final step's
`done` falls back to a `303` redirect to
`/request/<id>/audit/?just=1` — the same result page the workflow always
landed on. The `data-next-*` attributes are inert without a runtime, so
the no-JS path is byte-for-byte the original flow with one status code
changed from `302` to `303`. The `tests/test_e2e.py` suite asserts both
paths: `TestModalWizardFlagship` walks the partial envelopes, the
no-runtime regression lives in `TestSuccessRedirect`.

The shared `dialog` component
([`examples/_shared/_components/dialog/`](../_shared/_components/dialog/))
is now a pure styling shell over `<dialog>`. The framework's layer runtime
owns opening a dialog from a `data-next-layer` link and closing it on
accept or dismiss, so the component ships no open trigger of its own. Its
`component.mjs` keeps only the document-delegation idiom — a single
document listener that survives a morph replacing the dialog markup — for
the cases that mount a styled `<dialog>` directly without a layer.

#### Smoke checklist for the modal

The test suite asserts the server contract — the envelopes, the zone
targets, the no-JS redirect — but a few edges of a native `<dialog>` only
show up in a real browser. After any change to the modal flow, open
`/` with the runtime loaded and check by hand:

- **Modality and focus trap.** Opening the modal dims the page behind it
  and `Tab` cycles only inside the dialog, never reaching the list or the
  nav underneath.
- **Focus return.** Closing the modal — by submitting the last step, by
  `Esc`, or by clicking the backdrop — returns focus to the "Start a new
  request" link that opened it.
- **Caret and typed input.** A blur-validation error on the email field
  re-renders the step without moving the caret or clearing text already
  typed into a neighbouring field.
- **Geometry.** The dialog is centered, scrolls its own body when the
  step is tall, and the backdrop covers the full viewport with no gap at
  the page edges.
- **Toast and list refresh.** The success toast appears once on submit,
  and the recent-requests list under the closed modal shows the new row
  without a full page reload.
- **Lazy audit table.** Opening `/admin/audit/` shows the skeleton first,
  and the table fills in once it scrolls into view, with the `?kind=`
  filter applied on the zone request the same way it was on the full page.

## Further reading

- [`next/forms/wizard.py`](../../next/forms/wizard.py) — the declarative
  `FormWizard` base class, the `FormWizardBackend` contract, the default
  `SessionFormWizardBackend` this example builds on, and the optional
  `CacheFormWizardBackend`.
- [`next/forms/manager.py`](../../next/forms/manager.py) — the lazy,
  settings-driven `FormActionManager` used by every example.
- [`next/forms/backends.py`](../../next/forms/backends.py) — the
  `FormActionBackend` ABC and `RegistryFormActionBackend` superclass.
- [`next/forms/dispatch.py`](../../next/forms/dispatch.py) — where
  `action_dispatched`, `form_validation_failed`, `wizard_step_submitted`,
  `wizard_completed`, and `form_access_denied` are sent, and where the
  `check_permissions` / `has_object_permission` hooks run.
- [`next/forms/checks.py`](../../next/forms/checks.py) — `next.E041`
  (duplicate handlers), `next.E044` (bad backend config), `next.E045`
  (wrong backend type).
- [`next/testing/signals.py`](../../next/testing/signals.py) —
  `SignalRecorder` and `capture_signals` helpers used in the tests.
- [`docs/content/topics/testing.rst`](../../docs/content/topics/testing.rst)
  — canonical conftest scaffold mirrored in this example.
