# Django admin on next.dj

A full Django admin UI rebuilt on next.dj. Every page reads data straight
from `django.contrib.admin` (`AdminSite._registry`,
`ModelAdmin.get_changelist_instance`, `ModelAdmin.get_form`,
`get_inline_instances`, `get_actions`, `save_model`, `delete_model`,
`log_addition` / `log_change` / `log_deletions`), and none of
`django/contrib/admin/templates/` ends up in the response. The HTML is
shadcn-style Tailwind from the shared kit in
[`../_shared/_components/`](../_shared/_components/).

The example focuses on three reusable patterns. A request-aware
`@action(form_class=...)` factory that turns each `ModelAdmin.get_form`
result into a per-request `Form` class. A composite component
(`admin_form`) that owns both the template and the action handlers for
add and change. A single layout that branches between admin chrome and
the centered auth chrome through one `{% if is_auth_page %}` and one
`{% block template %}`.

## What you will see

| URL | Description |
|-----|-------------|
| `/admin/login/` | Username and password sign-in. `AdminPermissionMiddleware` redirects every other path here for anonymous or non-staff users. |
| `/admin/` | Dashboard with one card per registered app and quick links to each model's changelist and Add page. |
| `/admin/<app_label>/<model_name>/` | Changelist. `list_display` columns, sortable headers (`?o=<n>`), search box (when `search_fields` is set), `list_filter` aside, paginated results, bulk-action bar. |
| `/admin/<app_label>/<model_name>/add/` | Add view. Fieldsets from `ModelAdmin.get_fieldsets`, widgets from `ModelAdmin.get_form`, inline formsets. |
| `/admin/<app_label>/<model_name>/<pk>/change/` | Change view. Bound form plus inline formsets bound to existing rows. |
| `/admin/<app_label>/<model_name>/<pk>/delete/` | Confirmation page. Lists protected references and dependent objects from `admin.utils.get_deleted_objects`. |
| `/admin/<app_label>/<model_name>/<pk>/history/` | `LogEntry` rows for the object with Added / Changed / Deleted labels and the change message. |

A `library` app ships demo models. `Author`, `Tag`, `Book` (FK to
Author, M2M to Tag, autocomplete on `author`, `filter_horizontal` on
`tags`), and `Chapter` (inline under Book). The combination exercises
every flow above.

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

### 1. One layout, two shells

[`shadcn_admin/pages/layout.djx`](shadcn_admin/pages/layout.djx) keeps a
single `{% block template %}` and wraps it differently based on a
context flag. Django rejects two `{% block template %}` placeholders in
the same template, so the chrome branches sit **around** the block, not
inside two competing branches.

```djx
{% if is_auth_page %}
  <main class="flex min-h-screen items-center justify-center">
{% else %}
  <div class="flex min-h-screen">
    {% component "admin_sidebar" app_list=app_list %}
    ...topbar...
    <main class="flex-1">
{% endif %}

{% block template %}{% endblock template %}
```

`is_auth_page` comes from
[`shadcn_admin/pages/page.py`](shadcn_admin/pages/page.py) through
`@context("is_auth_page", inherit_context=True)`. It returns
`request.path.startswith` against `/admin/login/` and `/admin/logout/`,
so every descendant page picks it up without restating the rule.

### 2. Resolving `(model, ModelAdmin)` once

[`shadcn_admin/utils.py`](shadcn_admin/utils.py) holds
`resolve_model_admin(app_label, model_name)`. It calls
`apps.get_model` (raising `Http404` for unknown apps through `LookupError`)
and reads `admin.site._registry` (raising `Http404` for models that
exist but are not registered). Every admin page imports the helper, so
the 404 contract lives in one file.

`iter_app_list` in [`shadcn_admin/pages/page.py`](shadcn_admin/pages/page.py)
rebuilds the structure of `AdminSite.get_app_list` without calling
`reverse("admin:...")`. The classic admin URLConf is not mounted, and
the dashboard URLs are written explicitly against our own routing.

### 3. Changelist via `ModelAdmin.get_changelist_instance`

[`shadcn_admin/pages/[str:app_label]/[str:model_name]/page.py`](shadcn_admin/pages/%5Bstr%3Aapp_label%5D/%5Bstr%3Amodel_name%5D/page.py)
asks `ModelAdmin.get_changelist_instance(request)` and packs the result
for the template. The synthetic `action_checkbox` column that Django
injects into `cl.list_display` is filtered out because we render
selection ourselves through the `selectable=` prop on `data_table`.
Action labels are interpolated against the model's `verbose_name` and
`verbose_name_plural` so `delete_selected`'s `%(verbose_name_plural)s`
placeholder becomes `Delete selected books`.

The
[`data_table`](shadcn_admin/_components/data_table/component.djx)
component renders rows plus selection checkboxes. Its
[`.mjs`](shadcn_admin/_components/data_table/component.mjs) wires a
header checkbox that flips every row selection in vanilla DOM, no
framework. The
[`filters_panel`](shadcn_admin/_components/filters_panel/component.djx),
[`search_box`](shadcn_admin/_components/search_box/component.djx), and
[`admin_pagination`](shadcn_admin/_components/admin_pagination/component.djx)
components consume the same packed specs.

### 4. CRUD forms through a request-aware factory

`ModelAdmin.get_form(request, obj, change=...)` is request-dependent
(widgets, fieldsets, autocomplete choices). It cannot be captured as a
static `form_class`. The example's
[`admin_form` component](shadcn_admin/_components/admin_form/component.py)
exposes two factories, passes them to `@action(form_class=...)`, and the
dispatcher resolves each one per request before binding POST data.

```python
@action("admin:add", form_class=admin_add_form_factory)
def handle_add(request, form, app_label, model_name): ...

@action("admin:change", form_class=admin_change_form_factory)
def handle_change(request, form, app_label, model_name, pk): ...
```

Each factory calls `ModelAdmin.get_form(...)`, attaches `get_initial`
returning the model instance (so dispatch's `_build_form` takes the
ModelForm `instance=` branch), and returns the resulting class.
`@component.context("form_state")` rebuilds the same form on GET and
POST so a re-render after a validation failure preserves user input.

The single template file
[`admin_form/component.djx`](shadcn_admin/_components/admin_form/component.djx)
uses one `{% form @action=form_state.action_name %}` block. The action
name resolves at render time from the context value (see core change
below).

### 5. Inline formsets

`_build_inline_formsets` in the same component walks
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

### 6. Delete with `get_deleted_objects`

[`.../[int:pk]/delete/page.py`](shadcn_admin/pages/%5Bstr%3Aapp_label%5D/%5Bstr%3Amodel_name%5D/%5Bint%3Apk%5D/delete/page.py)
calls
`admin.utils.get_deleted_objects([obj], request, model_admin.admin_site)`
to compute the dependency tree, protected references, and missing
permissions. The confirmation template disables the submit button when
the action is blocked. On confirm, `ModelAdmin.log_deletions` plus
`delete_model` run and the user lands on the changelist.

### 7. History from `LogEntry`

[`.../[int:pk]/history/page.py`](shadcn_admin/pages/%5Bstr%3Aapp_label%5D/%5Bstr%3Amodel_name%5D/%5Bint%3Apk%5D/history/page.py)
queries
`LogEntry.objects.filter(content_type=ct, object_id=pk).select_related("user")`
and labels rows by `action_flag` (1 Added, 2 Changed, 3 Deleted).

### 8. Auth guard

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

Two small backward-compatible changes landed in next.dj alongside the
example.

* **`@action(form_class=callable)`.** A factory callable is accepted
  beside a `Form` subclass.
  [`next/forms/dispatch.py`](../../next/forms/dispatch.py) gains a
  `_resolve_form_class` helper that runs the factory through
  `resolver.resolve_dependencies` once per request, so admin pages can
  produce a class shaped by `ModelAdmin.get_form()` on the fly.
* **`{% form @action=variable %}`.** The tag now accepts a context
  variable, not only a string literal.
  [`next/templatetags/forms.py`](../../next/templatetags/forms.py)
  compiles the value through `parser.compile_filter`, so the
  `admin_form` template can keep one `{% form %}` block and resolve
  `admin:add` or `admin:change` from `form_state.action_name` at render
  time.

Existing form-action code is unaffected. Passing a `Form` subclass and a
quoted action name keeps the original control flow.

## Further reading

- [`next/forms/decorators.py`](../../next/forms/decorators.py) and
  [`next/forms/dispatch.py`](../../next/forms/dispatch.py) for the
  `@action` factory contract.
- [`next/templatetags/forms.py`](../../next/templatetags/forms.py) for
  `{% form %}` argument compilation.
- [`docs/content/guide/forms.rst`](../../docs/content/guide/forms.rst)
  for the form-action lifecycle.
