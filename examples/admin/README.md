# Django admin on next.dj

A full Django admin UI rebuilt on next.dj. Every page reads data straight
from `django.contrib.admin` (`AdminSite._registry`,
`ModelAdmin.get_changelist_instance`, `ModelAdmin.get_form`,
`get_inline_instances`, `get_actions`, `save_model`, `delete_model`,
`log_addition` / `log_change` / `log_deletions`), and none of
`django/contrib/admin/templates/` ends up in the response. The HTML is
shadcn-style Tailwind from the shared kit in
[`../_shared/_components/`](../_shared/_components/).

The example shows six reusable patterns. A request-aware
`@action(form_class=...)` factory that turns each `ModelAdmin.get_form`
result into a per-request `Form` class. A composite component
(`admin_form`) that owns both the template and the action handlers for
add and change, with all per-request state collapsed into one frozen
`AdminFormSpec` dataclass. Inline-formset validation that runs inside
the main form's `clean()`, so failures route through the framework's
re-render path instead of a 400. A single layout that branches between
admin chrome and the centered auth chrome through one `{% if
is_auth_page %}` and one `{% block template %}`. An audit feed wired
to `action_dispatched`, recording every admin dispatch through a single
receiver without touching the handlers themselves. And
`django.contrib.messages` plumbed end-to-end — handlers write through
`messages.success` / `messages.error`, `ModelAdmin.message_user` from
custom bulk actions flows through the same channel, and one
`flash_messages` component drains the queue at the top of every
page.

## What you will see

| URL | Description |
|-----|-------------|
| `/admin/login/` | Username and password sign-in. `AdminPermissionMiddleware` redirects every other path here for anonymous or non-staff users. |
| `/admin/` | Dashboard with one card per registered app and quick links to each model's changelist and Add page. |
| `/admin/<app_label>/<model_name>/` | Changelist with `list_display` columns, sortable headers (`?o=<n>`), search box (`search_fields`), `list_filter` aside, pagination, bulk-action bar. |
| `/admin/<app_label>/<model_name>/add/` | Add view. Fieldsets from `ModelAdmin.get_fieldsets`, widgets from `ModelAdmin.get_form`, inline formsets. |
| `/admin/<app_label>/<model_name>/<pk>/change/` | Change view. Bound form plus inline formsets bound to existing rows. |
| `/admin/<app_label>/<model_name>/<pk>/delete/` | Confirmation page. Lists protected references and dependent objects from `admin.utils.get_deleted_objects`. |
| `/admin/<app_label>/<model_name>/<pk>/history/` | `LogEntry` rows for the object with Added / Changed / Deleted labels and the change message. |
| `/admin/activity/` | Activity feed from `admin_audit.AdminActivityLog`, written by an `action_dispatched` receiver. One row per `admin:add` / `admin:change` / `admin:delete` / `admin:bulk_action`. |

A `library` app ships demo models. `Author`, `Tag`, `Book` (FK to
Author, M2M to Tag, `autocomplete_fields=("author",)`,
`filter_horizontal=("tags",)`, `is_featured: BooleanField` for the
checkbox flow, custom `mark_as_published` action), and `Chapter`
(inline under Book). The combination exercises every flow above —
text inputs, textarea, selects single and multi, checkbox, date,
number, autocomplete, and tabular inlines.

A second Django app `admin_audit` ships one model
(`AdminActivityLog`) and one signal receiver. It hangs off the
framework's `action_dispatched` signal and writes a row per dispatch.
No change to `library` or `shadcn_admin` is required for it to work.

## How to run

```bash
cd examples/admin
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver     # http://127.0.0.1:8000/admin/
uv run pytest
```

Tailwind loads via the Play CDN in the shared
[`page_head`](../_shared/_components/page_head/component.djx) component.
No Node, no build step. Sessions and CSRF use the Django defaults set in
[`config/settings.py`](config/settings.py).

## Walking the code

### 1. Two-layer layout: chrome envelope, surfaces shell

The router walks two roots, listed in
[`config/settings.py`](config/settings.py) under
`NEXT_FRAMEWORK["DEFAULT_PAGE_BACKENDS"]`:

* `DIRS = ["chrome"]` — the project-level page root. It contains a
  single [`chrome/layout.djx`](chrome/layout.djx) with the outermost
  HTML envelope: `<!DOCTYPE html>`, `<body>`, `{% component "page_head" %}`,
  and `{% collect_scripts %}`. Every page in the project gets wrapped
  by this layer first.
* `APP_DIRS = True` and `PAGES_DIR = "surfaces"` — each installed app
  may ship a `surfaces/` tree. `shadcn_admin/surfaces/` owns the actual
  pages (dashboard, login, logout, changelist, add, change, delete,
  history, activity) and their per-section `layout.djx` files.

[`shadcn_admin/surfaces/layout.djx`](shadcn_admin/surfaces/layout.djx) sits
**inside** the chrome envelope and keeps a single `{% block template %}`
wrapped differently based on a context flag. Django rejects two
`{% block template %}` placeholders in the same template, so the chrome
branches sit **around** the block, not inside two competing branches.

```djx
{% if is_auth_page %}
  <main class="flex min-h-screen items-center justify-center">
{% else %}
  <div class="flex min-h-screen">
    {% component "admin_sidebar" app_list=app_list %}
    ...topbar...
    <main class="flex-1">
{% endif %}

{% component "flash_messages" %}
{% block template %}{% endblock template %}
```

`is_auth_page` comes from
[`shadcn_admin/surfaces/page.py`](shadcn_admin/surfaces/page.py) through
`@context("is_auth_page", inherit_context=True)`. It returns
`request.path.startswith` against `/admin/login/` and `/admin/logout/`,
so every descendant page picks it up without restating the rule.

Component lookup is configured the same way:
`shadcn_admin/_panels/` (admin-specific composites: `admin_form`,
`admin_sidebar`, `data_table`, …) plus the shared shadcn kit in
[`../_shared/_components/`](../_shared/_components/) (the `_panels` and
`_components` names are deliberately different so the rename
demonstrates that the directory names are user-controlled).

### 2. Resolving `(model, ModelAdmin)` once

[`shadcn_admin/utils.py`](shadcn_admin/utils.py) holds
`resolve_model_admin(app_label, model_name)`. It calls
`apps.get_model` (raising `Http404` for unknown apps through `LookupError`)
and reads `admin.site._registry` (raising `Http404` for models that
exist but are not registered). Every admin page imports the helper, so
the 404 contract lives in one file.

`iter_app_list` in [`shadcn_admin/surfaces/page.py`](shadcn_admin/surfaces/page.py)
rebuilds the structure of `AdminSite.get_app_list` without calling
`reverse("admin:...")`. The classic admin URLConf is not mounted, and
the dashboard URLs are written explicitly against our own routing.

### 3. Changelist via `ModelAdmin.get_changelist_instance`

[`shadcn_admin/surfaces/[str:app_label]/[str:model_name]/page.py`](shadcn_admin/surfaces/%5Bstr%3Aapp_label%5D/%5Bstr%3Amodel_name%5D/page.py)
asks `ModelAdmin.get_changelist_instance(request)` and packs the result
for the template. The `changelist_state` callable is a thin orchestrator
that delegates to `_columns`, `_rows`, `_pagination`, `_filters`, and
`_actions` helpers in the same file, so the response shape fits on one
screen. The synthetic `action_checkbox` column that Django injects into
`cl.list_display` is filtered out because we render selection ourselves
through the `selectable=` prop on `data_table`. Action labels are
interpolated against the model's `verbose_name` and `verbose_name_plural`
so `delete_selected`'s `%(verbose_name_plural)s` placeholder becomes
`Delete selected books`.

The
[`data_table`](shadcn_admin/_panels/data_table/component.djx)
component renders rows plus selection checkboxes. Its
[`.mjs`](shadcn_admin/_panels/data_table/component.mjs) wires a
header checkbox that flips every row selection in vanilla DOM, no
framework. The
[`filters_panel`](shadcn_admin/_panels/filters_panel/component.djx),
[`search_box`](shadcn_admin/_panels/search_box/component.djx), and
[`admin_pagination`](shadcn_admin/_panels/admin_pagination/component.djx)
components consume the same packed specs.

### 4. CRUD forms through a request-aware factory

`ModelAdmin.get_form(request, obj, change=...)` is request-dependent
(widgets, fieldsets, autocomplete choices). It cannot be captured as a
static `form_class`. The example's
[`admin_form` component](shadcn_admin/_panels/admin_form/component.py)
exposes two factories, passes them to `@action(form_class=...)`, and the
dispatcher resolves each one per request before binding POST data.

```python
@action("admin:add", form_class=admin_add_form_factory)
def handle_add(request, form, app_label, model_name): ...

@action("admin:change", form_class=admin_change_form_factory)
def handle_change(request, form, app_label, model_name, pk): ...
```

Both factories and the `form_state` context route through one frozen
dataclass — `AdminFormSpec(request, app_label, model_name, model,
model_admin, instance)`. `AdminFormSpec.resolve(...)` calls
`apps.get_model`, reads `admin.site._registry`, and calls
`ModelAdmin.get_object(request, pk)` exactly once. The factory wraps
the base form into an `AdminForm` whose `get_initial()` returns the
instance (so dispatch's `_build_form` takes the ModelForm `instance=`
branch) and whose class attribute `_admin_spec` exposes the spec to
signal receivers downstream.

`@component.context("form_state")` rebuilds the same form on GET and
POST so a re-render after a validation failure preserves user input.
Widget descriptors flow as `WidgetInfo` instances — another frozen
dataclass with one job: map each `BoundField` to a stable `kind`
(`textarea`/`checkbox`/`select`/`select_multi`/`input`) so the
[`form_field` template](shadcn_admin/_panels/form_field/component.djx)
never has to know widget class names. The mapping uses `isinstance`
against base widgets (`django_forms.Textarea`, `SelectMultiple`,
`Select`, …) so it covers Django's admin subclasses
(`AdminTextareaWidget`, `AdminDateWidget`, …) and any third-party
widgets in the same hierarchy without enumerating names. `input_type`
is read straight from the widget (`widget.input_type`) so HTML5 types
(`email`, `number`, `date`, `password`, `url`) propagate without an
extra lookup table.

The single template file
[`admin_form/component.djx`](shadcn_admin/_panels/admin_form/component.djx)
uses one `{% form @action=form_state.action_name %}` block. The action
name resolves at render time from the context value (see core change
below).

### 5. Inline formsets validate alongside the main form

`_build_inline_formsets(spec)` walks
`model_admin.get_inline_instances(request, obj)`, calls
`inline.get_formset(request, obj)`, and binds the resulting formset to
`request.POST` on POST. For empty extra rows (`form.empty_permitted and
not form.instance.pk`) it drops `form.initial` and each
`field.initial`, so the rendered inputs are blank and `has_changed()`
stays `False` when the user submits an unfilled row. Without the reset,
Django would treat the model-default values (for example
`Chapter.word_count=0`) as `initial`, see them differ from the
submitted empty string, mark the form as changed, and run required
validation on the row.

Inline validation runs **inside the main form's `clean()`**. When any
inline row fails, `clean()` raises `ValidationError`, so
`form.is_valid()` returns `False` and the framework re-renders the
origin page through its `form_validation_failed` machinery — the
inputs stay populated, the inline rows show their per-field errors,
and the user never sees a bare 400. No core change is required for
this. `Form.clean()` raising `ValidationError` is the same path the
framework already uses for field-level errors.

### 6. Save and continue / Save and add another

The form template renders three submit buttons:

* **Save** — default, redirects to the changelist on success.
* **Save and continue editing** (`name="_save_continue"`) — redirects
  to the change view for the just-saved object.
* **Save and add another** (`name="_save_addanother"`) — redirects to
  the add view so the operator can enter another record.

`_redirect_after_save(spec, obj)` reads `request.POST` for the two
button names and branches accordingly. Both add and change handlers
share the same routing through `_persist`, so the buttons work in
either flow.

### 7. Custom bulk action `mark_as_published`

[`library/admin.py`](library/admin.py) declares an `@admin.action` on
`BookAdmin` that flips selected books to the `published` status. The
existing changelist action bar (rendered from `model_admin.get_actions`)
picks it up automatically and the `admin:bulk_action` handler routes the
POST through `model_admin.response_action(...)`, the same code path as
the built-in `delete_selected` action. The interpolated description
"Mark selected books as published" comes from
`%(verbose_name_plural)s` resolution inside the changelist serializer.

### 8. Audit feed driven by `action_dispatched`

The [`admin_audit`](admin_audit/) Django app records every admin
dispatch through a single signal receiver:

```python
@receiver(action_dispatched)
def log_admin_action(action_name="", form=None, url_kwargs=None,
                     response_status=0, **_):
    ...
    spec = getattr(form, "_admin_spec", None) if form is not None else None
    user = spec.request.user if spec and spec.request.user.is_authenticated else None
    AdminActivityLog.objects.create(...)
```

`action_dispatched` carries the bound `form` and a copy of `url_kwargs`
out of the box. The form-bearing actions (`admin:add`, `admin:change`)
expose the originating request through the `_admin_spec` class attribute
attached by `_build_form_class`, so the receiver captures the user. The
form-less actions (`admin:delete`, `admin:bulk_action`) come through
with `form=None`, so those rows record action + target + status but
leave the user field empty. The
[`/admin/activity/` page](shadcn_admin/surfaces/activity/) reads the log
and renders the latest 50 rows.

### 9. Flash messages through `django.contrib.messages`

Every success path writes a flash before redirecting:

* `_persist` (add / change) →
  `messages.success(request, "The book Sample was added successfully.")`
* delete handler → `messages.success(request, "The book Sample was deleted successfully.")`
* login handler → `messages.error(request, "Invalid username or password.")`
  or `messages.success(request, "Welcome, admin.")`
* `BookAdmin.mark_as_published` →
  `self.message_user(request, "N book(s) marked as published.")`
  (Django's stock `ModelAdmin.message_user` writes through the same
  framework.)

The
[`flash_messages` component](shadcn_admin/_panels/flash_messages/component.py)
drains pending messages off the request, maps Django's level tags
(`success` / `error` / `warning` / `info`) to the shared `alert`
component's variants, and is invoked once from `layout.djx` so both
the admin chrome and the auth chrome surface flashes without repeating
HTML.

### 10. Delete with `get_deleted_objects`

[`.../[int:pk]/delete/page.py`](shadcn_admin/surfaces/%5Bstr%3Aapp_label%5D/%5Bstr%3Amodel_name%5D/%5Bint%3Apk%5D/delete/page.py)
calls
`admin.utils.get_deleted_objects([obj], request, model_admin.admin_site)`
to compute the dependency tree, protected references, and missing
permissions. The confirmation template disables the submit button when
the action is blocked. On confirm, `ModelAdmin.log_deletions` plus
`delete_model` run and the user lands on the changelist.

### 11. History from `LogEntry`

[`.../[int:pk]/history/page.py`](shadcn_admin/surfaces/%5Bstr%3Aapp_label%5D/%5Bstr%3Amodel_name%5D/%5Bint%3Apk%5D/history/page.py)
queries
`LogEntry.objects.filter(content_type=ct, object_id=pk).select_related("user")`
and labels rows by `action_flag` (1 Added, 2 Changed, 3 Deleted).

### 12. Auth guard

[`shadcn_admin/middleware.py`](shadcn_admin/middleware.py) is one class
that calls `admin.site.has_permission(request)` for paths under
`/admin/`, skipping `/admin/login/`, `/admin/_next/` (where next.dj
mounts its form-action endpoints), and `/static/`. Failing requests
redirect to `/admin/login/?next=<path>`.

Login uses a plain `Form` (username and password) and authenticates
manually inside the action handler. The handler is decoupled from
`AuthenticationForm` on purpose. The latter's `__init__(self, request,
data=...)` signature collides with the dispatcher's
`form_class(post_data, files, ...)` call.

## What is in core for this example

Four pre-existing pieces of next.dj's form layer plus one small core
fix carry the example.

* **`@action(form_class=callable)`.**
  [`next/forms/dispatch.py`](../../next/forms/dispatch.py) accepts a
  callable beside a `Form` subclass. `_resolve_form_class` runs the
  factory through `resolver.resolve_dependencies` once per request, so
  admin pages produce a class shaped by `ModelAdmin.get_form()` on the
  fly.
* **`{% form @action=variable %}`.** The tag accepts a context
  variable, not only a string literal.
  [`next/templatetags/forms.py`](../../next/templatetags/forms.py)
  compiles the value through `parser.compile_filter`, so the
  `admin_form` template keeps one `{% form %}` block and resolves
  `admin:add` or `admin:change` from `form_state.action_name` at render
  time.
* **`action_dispatched` carries `form` and `url_kwargs`.**
  [`next/forms/dispatch.py`](../../next/forms/dispatch.py) sends both
  fields on every successful dispatch (the form is `None` for handlers
  without a `form_class`).
  [`admin_audit.signals.log_admin_action`](admin_audit/signals.py) reads
  them to write one `AdminActivityLog` row per dispatch without ever
  touching the handlers it observes.
* **`Form.clean()` → `ValidationError` re-renders the origin page.**
  The same path the framework already uses for field-level errors lets
  `AdminForm.clean()` validate inline formsets and surface their errors
  on the bound form, with the user's typed data preserved.
* **Virtual pages survive form re-render.**
  [`next/forms/uid.py`](../../next/forms/uid.py) now accepts a
  `_next_form_page` whose `page.py` does not exist on disk as long as
  a sibling `template.djx` does — matching the same virtual-page rule
  the file router already applies. That lets the add and change views
  in this example live as `template.djx`-only directories (no empty
  docstring-only `page.py` anchor) and still re-render correctly on
  validation failure.

Existing form-action code is unaffected. Passing a `Form` subclass and a
quoted action name keeps the original control flow.

## Further reading

- [`next/forms/decorators.py`](../../next/forms/decorators.py) and
  [`next/forms/dispatch.py`](../../next/forms/dispatch.py) for the
  `@action` factory contract.
- [`next/forms/signals.py`](../../next/forms/signals.py) for the
  `action_dispatched` / `form_validation_failed` payload contracts.
- [`next/templatetags/forms.py`](../../next/templatetags/forms.py) for
  `{% form %}` argument compilation.
- [`docs/content/guide/forms.rst`](../../docs/content/guide/forms.rst)
  for the form-action lifecycle, including the factory pattern used by
  sections 4 and 5 above.
- [`docs/content/guide/pages-and-templates.rst`](../../docs/content/guide/pages-and-templates.rst)
  for the chrome / surfaces layering shown in section 1.
- [`docs/content/guide/components.rst`](../../docs/content/guide/components.rst)
  for the component lookup configured under `_panels/`.
- [`docs/content/guide/context.rst`](../../docs/content/guide/context.rst)
  for `@context` and `inherit_context` used by `app_list` and
  `is_auth_page`.
- [`docs/content/guide/dependency-injection.rst`](../../docs/content/guide/dependency-injection.rst)
  for `@resolver.dependency` and `Depends("name")` used by the
  `admin_spec` registration in section 4.
- [`docs/content/guide/file-router.rst`](../../docs/content/guide/file-router.rst)
  for the `[str:app_label]/[int:pk]/` directory naming under
  `surfaces/`.
