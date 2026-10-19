# Django admin on next.dj

A full Django admin UI rebuilt on next.dj. Every page reads data straight from `django.contrib.admin` (`AdminSite._registry`, `ModelAdmin.get_changelist_instance`, `ModelAdmin.get_form`, `get_inline_instances`, `get_action_choices`, `save_model`, `delete_model`, `log_addition` / `log_change` / `log_deletions`), and none of `django/contrib/admin/templates/` ends up in the response. The HTML is shadcn-style Tailwind from the shared kit in [`../_shared/_components/`](../_shared/_components/).

The example shows eight reusable patterns. A request-aware `@action(form_class=...)` factory that turns each `ModelAdmin.get_form` result into a per-request `Form` class. A shared actions module ([`shadcn_admin/forms.py`](shadcn_admin/forms.py)) that owns the add and change factories and handlers, with all per-request state collapsed into one frozen `AdminFormSpec` dataclass shared through `Depends("admin_spec")`. The `admin_form` composite keeps only the template and its `form_state` render context. Access declared on the page tree, a staff login the dashboard `page.py` passes down its tree with `inherit=True` over a login that `NEXT_FRAMEWORK["LOGIN_REQUIRED"]` asks of every page, with the sign-in pages lifted out of both through `page.public()`. Declarative guards on every mutating action, `login_required=True` at registration plus the matching `ModelAdmin` permission check inside the handler. Inline-formset validation that runs inside the main form's `clean()`, so failures route through the framework's re-render path instead of a 400. A single project-level layout that branches between admin chrome and the centered auth chrome through one `{% if is_auth_page %}` and one `{% template %}`. An audit feed wired to `action_dispatched`, recording every admin dispatch through a single receiver without touching the handlers themselves, with its feed page served from the `pages/` tree of the app that owns the log. And `django.contrib.messages` plumbed end-to-end, where handlers write through `messages.success` / `messages.error`, `ModelAdmin.message_user` from custom bulk actions flows through the same channel, and one `flash_messages` component drains the queue at the top of every page.

## What you will see

| URL | Description |
| --- | --- |
| `/` | Redirect to the dashboard. [`config/urls.py`](config/urls.py) includes next.dj under `admin/` and points the site root at `reverse_lazy("next:page_")`, the router's name for the dashboard in `shadcn_admin/pages/`. |
| `/admin/login/` | Username and password sign-in. Every other page redirects an anonymous visit here, and a signed-in user without staff gets `403` (section 12). |
| `/admin/logout/` | Signed-out farewell page. The topbar Sign out form posts `admin:logout`, which clears the session and lands here. |
| `/admin/` | Dashboard with one card per registered app, quick links to each model's changelist and Add page, and a Recent activity panel that refreshes every five seconds for accounts that may read the audit log. |
| `/admin/<app_label>/<model_name>/` | Changelist with `list_display` columns, sortable headers (`?o=<n>`), search box (`search_fields`), `list_filter` aside, pagination, bulk-action bar. |
| `/admin/<app_label>/<model_name>/add/` | Add view. Fieldsets from `ModelAdmin.get_fieldsets`, widgets from `ModelAdmin.get_form`, inline formsets. |
| `/admin/<app_label>/<model_name>/<pk>/change/` | Change view. Bound main form plus a keyed `admin:inline_change` form per existing related row and an `admin:inline_add` form for new ones. |
| `/admin/<app_label>/<model_name>/<pk>/delete/` | Confirmation page. Lists protected references and dependent objects from `admin.utils.get_deleted_objects`. |
| `/admin/<app_label>/<model_name>/<pk>/history/` | `LogEntry` rows for the object with Added / Changed / Deleted labels and the change message. |
| `/admin/library/book/<book>/outline/` | The chapters of one book in reading order, opened from the Outline button on the book's change view and open to every admin, with a link to the manuscript wherever the user may change the book (section 15). |
| `/admin/library/book/<book>/manuscript/` | The same chapters with an Append chapter form. An editor reaches it on drafts of their own or of nobody, and everything else answers `403` (section 15). |
| `/admin/activity/` | Activity feed from `admin_audit.AdminActivityLog`, written by an `action_dispatched` receiver. One row per `admin:add` / `admin:change` / `admin:delete` / `admin:bulk_action`. Staff without `admin_audit.view_adminactivitylog` get `403`. |

A `library` app ships demo models. `Author`, `Tag`, `Book` (FK to Author, an optional FK `editor` to the account that works it, M2M to Tag, `autocomplete_fields=("author",)`, `filter_horizontal=("tags",)`, `is_featured: BooleanField` for the checkbox flow, custom `mark_as_published` action), and `Chapter` (inline under Book). The combination exercises every flow above: text inputs, textarea, selects single and multi, checkbox, date, number, autocomplete, and tabular inlines. The app also ships a page tree of its own with two pages under each book, the outline and the manuscript of section 15.

[`library/demo.py`](library/demo.py) holds the demo catalog and the `seed_demo` management command writes it, so every row of the table above has data behind it. It seeds 19 public-domain books by 7 authors under 6 tags, spread across all three `status` values, featured and plain, tagged and untagged, with and without a publication date, and chapters on two of them. `BookAdmin.list_per_page` is 12, so the book changelist opens on page 1 of 2 and every `list_filter` facet, including the `tags` "empty" branch, resolves to a non-empty result. Migrations carry schema only, so the catalog stays empty until you run the command, and a second run adds nothing. The command also creates two ordinary staff accounts, `editor` and `copyeditor`, each with its username as the password, whose rights section 14 narrows, and hands the books of each author to one of them, so `Mathilda` is a draft of `editor` and `The Mysterious Island` a draft of `copyeditor`. No superuser is seeded, so `createsuperuser` remains the step that gives you the full login.

A second Django app `admin_audit` ships one model (`AdminActivityLog`), one signal receiver, the activity feed page, and the two functions that add the feed to the sidebar and a Recent activity panel to the dashboard (section 12). The receiver hangs off the framework's `action_dispatched` signal and writes a row per dispatch, and the feed answers at `/admin/activity/` from the app's own `pages/activity/` directory (section 8).

## How to run

```bash
cd examples/admin
uv run python manage.py migrate          # builds the schema
uv run python manage.py seed_demo        # fills the demo catalog and adds two editors
uv run python manage.py createsuperuser  # the one account with every right
uv run python manage.py runserver        # http://127.0.0.1:8000/admin/
uv run pytest
```

Tailwind loads via the Play CDN in the shared [`page_head`](../_shared/_components/page_head/component.djx) component. No Node, no build step. Sessions and CSRF use the Django defaults set in [`config/settings.py`](config/settings.py).

## Walking the code

### 1. One project-level layout over every page tree

The router walks two kinds of roots, listed in [`config/settings.py`](config/settings.py) under `NEXT_FRAMEWORK["PAGE_BACKENDS"]`:

- `DIRS = ["chrome"]`: the project-level page root. It contains a single [`chrome/layout.djx`](chrome/layout.djx) with the HTML envelope (`<!DOCTYPE html>`, `<body>`, `{% component "page_head" %}`, `{% collect_scripts %}`) and the admin shell inside it. Every page of every tree is wrapped by this file and by no other layout.
- `APP_DIRS = True` and `PAGES_DIR = "pages"`: each installed app may ship page trees. `shadcn_admin/pages/` owns the dashboard, changelist, add, change, delete and history pages, plus the login and logout pages, which lift the rule of the tree with `page.public()` (section 12). `admin_audit/pages/` owns the activity feed and `library/pages/` the book outline and manuscript, each under the URL its directories spell.

The shell sits at project level because a `layout.djx` wraps only the pages of its own tree, and `inherit_context` reaches only the `page.py` files above a page in that tree. A shell in `shadcn_admin/pages/layout.djx` would leave the `admin_audit` pages bare, while the project root is the one layer every tree shares. The layout keeps a single `{% template %}` wrapped differently based on a context flag. A layout carries exactly one placeholder. Composition fills the first and `next.W078` flags the rest, so the chrome branches sit **around** the placeholder, not inside two competing branches.

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
{% template %}
```

`is_auth_page` and `app_list` come from `admin_shell` in [`shadcn_admin/context_processors.py`](shadcn_admin/context_processors.py), listed under the router's `OPTIONS["context_processors"]`, so they reach every page whichever tree it lives in. `is_auth_page` tests `request.path.startswith` against `/admin/login/` and `/admin/logout/`. The dashboard template reads the same `app_list` for its cards, so its `page.py` declares only its breadcrumb label (section 13) and the access rule of its tree (section 12). The price is that `shadcn_admin` no longer carries its own look, and a project installing it lists the processor and owns the layout.

Component lookup is configured the same way: `shadcn_admin/_components/` (admin-specific composites: `admin_form`, `admin_sidebar`, `data_table`, …) plus the shared shadcn kit in [`../_shared/_components/`](../_shared/_components/).

### 2. Resolving `(model, ModelAdmin)` once

[`shadcn_admin/utils.py`](shadcn_admin/utils.py) holds `resolve_model_admin(app_label, model_name)`. It calls `apps.get_model` (raising `Http404` for unknown apps through `LookupError`) and reads `admin.site._registry` (raising `Http404` for models that exist but are not registered). Every admin page imports the helper, so the 404 contract lives in one file.

`app_list` in [`shadcn_admin/context_processors.py`](shadcn_admin/context_processors.py) rebuilds the structure of `AdminSite.get_app_list` without calling `reverse("admin:...")`. The classic admin URLConf is not included, and the dashboard URLs are written explicitly against our own routing.

### 3. Changelist via `ModelAdmin.get_changelist_instance`

[`shadcn_admin/pages/[str:app_label]/[str:model_name]/page.py`](shadcn_admin/pages/%5Bstr%3Aapp_label%5D/%5Bstr%3Amodel_name%5D/page.py) asks `ModelAdmin.get_changelist_instance(request)` and packs the result for the template. The `changelist_state` callable is a thin orchestrator that delegates to `_columns`, `_rows`, `_pagination`, `_filters`, and `_actions` helpers in the same file, so the response shape fits on one screen. The synthetic `action_checkbox` column that Django injects into `cl.list_display` is filtered out because we render selection ourselves through the `selectable=` prop on `data_table`. The bulk-action list comes from `ModelAdmin.get_action_choices(request, default_choices=[])` rather than `get_actions`, because that method already interpolates the model's `verbose_name` and `verbose_name_plural` (`mark_as_published`'s `%(verbose_name_plural)s` placeholder arrives as `Mark selected books as published`), and its `(name, description)` pairs read the same on every supported Django version, while `get_actions` answers a `(callable, name, description)` tuple on 5.2 and an `Action` dataclass from 6.0 on.

`_pagination` repacks the page `ChangeList` already sliced from `ModelAdmin.list_per_page`, so the page size stays a `ModelAdmin` decision rather than a template constant. The [`data_table`](shadcn_admin/_components/data_table/component.djx) component renders rows plus selection checkboxes. Its [`.mjs`](shadcn_admin/_components/data_table/component.mjs) wires a header checkbox that flips every row selection in vanilla DOM, no framework. The [`filters_panel`](shadcn_admin/_components/filters_panel/component.djx), [`search_box`](shadcn_admin/_components/search_box/component.djx), and [`admin_pagination`](shadcn_admin/_components/admin_pagination/component.djx) components consume the same packed specs.

### 4. CRUD forms through a request-aware factory

`ModelAdmin.get_form(request, obj, change=...)` is request-dependent (widgets, fieldsets, autocomplete choices). It cannot be captured as a static `form_class`. The shared actions module [`shadcn_admin/forms.py`](shadcn_admin/forms.py) exposes two factories, passes them to `@action(form_class=...)`, and the dispatcher resolves each one per request before binding POST data.

```python
@action("admin:add", form_class=admin_add_form_factory, login_required=True)
def handle_add(form, spec=Depends("admin_spec")):
    if not spec.model_admin.has_add_permission(spec.request):
        raise PermissionDenied
    return _persist(form, spec, change=False)


@action("admin:change", form_class=admin_change_form_factory, login_required=True)
def handle_change(form, spec=Depends("admin_spec")):
    if not spec.model_admin.has_change_permission(spec.request, spec.instance):
        raise PermissionDenied
    return _persist(form, spec, change=True)
```

The factories, the handlers, and the `form_state` render context all route through one frozen dataclass, `AdminFormSpec(request, app_label, model_name, model, model_admin, instance)`. `AdminFormSpec.resolve(...)` calls `apps.get_model`, reads `admin.site._registry`, and calls `ModelAdmin.get_object(request, pk)` exactly once per dispatch: it is registered as a named dependency (`@resolver.dependency("admin_spec")` in [`shadcn_admin/deps.py`](shadcn_admin/deps.py), which the framework imports at startup), so every `Depends("admin_spec")` parameter within the same POST shares a single resolution. Both factories call `_build_form_class`, which derives an `AdminForm` from `ModelAdmin.get_form(...)` and `BaseModelForm`. Its `get_initial()` returns the instance, so the dispatcher passes it to the ModelForm as `instance=`, and its `clean()` rebuilds the inline formsets against the same POST so a broken row surfaces on the main form.

The [`admin_form` component](shadcn_admin/_components/admin_form/component.py) keeps the render side. `@component.context("form_state")` rebuilds the same form on GET and POST so a re-render after a validation failure preserves user input, and serialises it through the core `form_spec()` helper. Each resulting `FieldSpec` maps its `BoundField` to a stable `kind` (`textarea`/`checkbox`/`select`/`select_multi`/`input`) plus an `input_type` read straight from the widget, so the [`form_field` template](shadcn_admin/_components/form_field/component.djx) renders from `info.kind`, `info.bound`, and `info.is_extra` without knowing widget class names. Django's admin widget subclasses (`AdminTextareaWidget`, `AdminDateWidget`, …) and HTML5 input types (`email`, `number`, `date`, `password`, `url`) propagate without an extra lookup table.

`FieldSpec` names the shape of a field, not the constraints its form field derived, so a hand-written input would drop them. The co-located [`form_field/component.py`](shadcn_admin/_components/form_field/component.py) flattens `widget.attrs` into the tag and skips the handful of names the template spells out itself, so the shadcn class survives while `step="0.01"` on a `DecimalField` and `maxlength` on a `CharField` reach the browser. Without the `step` an `<input type="number">` falls back to whole numbers and the browser silently refuses to submit a price like `12.50`.

The single template file [`admin_form/component.djx`](shadcn_admin/_components/admin_form/component.djx) uses one `{% form form_state.action_name %}` block. The tag takes the action name as its positional argument. Here it is a context variable, so the action name resolves at render time (see core change below).

### 5. Inlines: batch on add, live keyed forms on change

The **add view** binds its related rows in one batch. `build_inline_formsets(spec)` in [`shadcn_admin/forms.py`](shadcn_admin/forms.py) walks `model_admin.get_inline_instances(request, obj)`, calls `inline.get_formset(request, obj)`, and binds the resulting formset to `request.POST` when its management form is present. For empty extra rows (`form.empty_permitted and not form.instance.pk`) it drops `form.initial` and each `field.initial`, so the rendered inputs are blank and `has_changed()` stays `False` when the user submits an unfilled row. Without the reset, Django would treat the model-default values (for example `Chapter.word_count=0`) as `initial`, see them differ from the submitted empty string, mark the form as changed, and run required validation on the row. Inline validation runs **inside the main form's `clean()`**: a broken row raises `ValidationError`, so `form.is_valid()` returns `False` and the framework re-renders the origin page with the inputs populated and the row errors shown.

The **change view** edits each existing related row on its own. `AdminInlineSpec` wraps one inline of the parent admin and renders every child as a keyed [`inline_row`](shadcn_admin/_components/inline_row/component.djx) form, so a chapter saves without touching its siblings:

```djx
{% form "admin:inline_change" key=row.pk %}
  <input type="hidden" name="_inline" value="{{ token }}">
  <input type="hidden" name="_inline_pk" value="{{ row.pk }}">
  {% for info in row.fields %}{% component "form_field" info=info %}{% endfor %}
  {% component "button" type="submit" text="Save" %}
{% endform %}
```

The form repeats once per row, so every instance shares one action UID. `key=` writes `data-next-key`, so an invalid submit re-renders the submitted row rather than the first. A looped `{% form %}` with neither a `key=` nor a wrapping `zone=` raises the `next.W070` system check.

`admin:inline_change` resolves the child model from the parent admin's inlines (`_inline` on the wire), the row by primary key (`_inline_pk`), and saves just that row. A trailing `{% form "admin:inline_add" %}` creates a new row through the same inline form class. On a partial request both author patch envelopes through `Patches(request)`:

- **change** → `replace` swaps the keyed row form wholesale for the freshly rendered saved form, and `inner` updates the section's `data-inline-count` badge in place.
- **add** → `layer_open(href=..., zone="record")` opens the _new row's own_ standalone change view `record` zone in a server-initiated result layer, and `inner` refreshes the parent's count badge. The client fetches that zone and morphs it into the modal, so adding a chapter drops the operator straight into editing its details rather than reopening the parent over itself.

The change swap reuses the `inline_row` component so the live markup never drifts from the page render. The add editor reuses the chapter's own generic change template, which is already wrapped in the `record` zone. Without a runtime each falls back to a redirect to the change view, and an invalid submit still re-renders the page with the bad row's errors, the rest untouched.

**Every id is namespaced, because two pages share one document.** Each row builds the same unprefixed form, so without help they would all render `id_title` and steal the parent form's label. The rows go through `auto_id` (`id_chapter_<pk>_title`, `id_chapter_add_title`) rather than a `prefix=`, because `auto_id` renames only the ids while the wire keeps posting the plain field names the handlers read. The main form gets the same treatment from `AdminFormSpec.auto_id` (`id_book_title`, `id_chapter_title`): once `layer_open` drops the chapter change view on top of the book change view, both editors live in one DOM, and a bare `id_%s` would put `id_title` in the document twice, so `<label for="id_title">` inside the modal would focus the book behind it.

**Uniqueness needs the parent key in scope.** `Chapter` declares `unique_together = [("book", "number")]`, but the foreign key back to the book is never a field on a keyed row form. Django's `ModelForm` excludes fields the form does not carry from model validation, so every unique check naming `book` is skipped and a duplicate chapter number reaches the database as an `IntegrityError`, which is a 500, not a form error. An inline formset avoids this by adding the parent link as a form field. A standalone row form has no formset to do that, so `_row_form_class` overrides `validate_unique()` to subtract the foreign key from the exclusion set Django computed:

```python
class InlineRowForm(base):
    def validate_unique(self):
        exclude = self._get_validation_exclusions() - {fk_name}
        try:
            self.instance.validate_unique(exclude=exclude)
        except ValidationError as exc:
            self._update_errors(exc)
```

The row's instance already carries the parent (an existing child through `_inline_pk`, a new one through `blank()`), so the pair check runs against the right book and a duplicate number comes back as a non-field error on that row. The same class binds the render and the dispatch, so the error shows up whether the submit was a partial morph or a full-page re-render.

The editor also renders a **Discard** form that posts `admin:discard`. The handler answers with `Patches(request).layer_close(dismiss="discarded")`, a server-side _dismissal_ rather than a `layer_close(result=...)` accept, so the client fires `partial:layer-dismissed` and skips any accept side effects. Whether the editor sits in a layer is a client-side fact the server cannot see, since a modal submit and a full-page submit arrive identical. The panel's co-located `component.css` therefore shows the button only under a `dialog` ancestor and the full-page change view keeps it hidden. Without a runtime the post falls back to a dashboard redirect.

### 6. Save and continue / Save and add another

The form template renders three submit buttons:

- **Save**: default, redirects to the changelist on success.
- **Save and continue editing** (`name="_save_continue"`) redirects to the change view for the just-saved object.
- **Save and add another** (`name="_save_addanother"`) redirects to the add view so the operator can enter another record.

`_redirect_after_save(spec, obj)` reads `request.POST` for the two button names and branches accordingly. Both add and change handlers share the same routing through `_persist`, so the buttons work in either flow.

### 7. Custom bulk action `mark_as_published`

[`library/admin.py`](library/admin.py) declares an `@admin.action` on `BookAdmin` that flips selected books to the `published` status. The existing changelist action bar (rendered from `model_admin.get_actions`) picks it up automatically and the `admin:bulk_action` handler routes the POST through `model_admin.response_action(...)`. Django's built-in `delete_selected` is switched off with `admin.site.disable_action("delete_selected")` in [`shadcn_admin/admin.py`](shadcn_admin/admin.py), because its confirmation step renders `django.contrib.admin`'s own templates and reverses into the `admin` URL namespace, neither of which this example routes. Deleting goes through the per-object delete page of section 10. The interpolated description "Mark selected books as published" comes from `%(verbose_name_plural)s` resolution inside the changelist serializer.

### 8. Audit feed driven by `action_dispatched`

The [`admin_audit`](admin_audit/) Django app records every admin dispatch through a single signal receiver in [`admin_audit/signals.py`](admin_audit/signals.py), which the framework imports at startup:

```python
@receiver(action_dispatched)
def log_admin_action(
    action_name="",
    form=None,
    url_kwargs=None,
    response_status=0,
    dep_cache=None,
    **kwargs,
):
    ...
    spec = (dep_cache or {}).get("admin_spec")
    user = None
    if spec is not None and spec.request.user.is_authenticated:
        user = spec.request.user
    AdminActivityLog.objects.create(...)
```

`action_dispatched` carries the bound `form`, a copy of `url_kwargs`, and `dep_cache` (the per-dispatch cache of resolved named dependencies) out of the box. The form-bearing actions (`admin:add`, `admin:change`) resolve `Depends("admin_spec")`, so the receiver reads the same `AdminFormSpec` the handler used from `dep_cache["admin_spec"]` and captures the user. The actions that never resolve that dependency (`admin:delete`, `admin:bulk_action`) come through without an `admin_spec` entry, so those rows record action + target + status but leave the user field empty. The [`/admin/activity/` page](admin_audit/pages/activity/page.py) reads the log and renders the latest 50 rows. The root of the tree declares `page.permission_required("admin_audit.view_adminactivitylog", inherit=True)` (section 12), so a staff account without that right, like the seeded `editor`, gets a `403` there as well as a sidebar without the app.

The page that reads the trail lives in the app that owns it. Every app tree joins the same `include("next.urls")`, so its directories spell the URL in any app, and [`admin_audit/pages/activity/page.py`](admin_audit/pages/activity/page.py) answers `/admin/activity/` under the name `page_activity`. `uv run python manage.py routes` prints the map with the file and the access rule of every page.

### 9. Flash messages through `django.contrib.messages`

Every success path writes a flash before redirecting:

- `_persist` (add / change) → `messages.success(request, "The book Sample was added successfully.")`
- delete handler → `messages.success(request, "The book Sample was deleted successfully.")`
- login handler → `messages.success(request, "Welcome, admin.")` (a failed sign-in never reaches the handler, it comes back as an `AuthenticationForm` non-field error)
- `BookAdmin.mark_as_published` → `self.message_user(request, "N book(s) marked as published.")` (Django's stock `ModelAdmin.message_user` writes through the same framework.)

The [`flash_messages` component](../_shared/_components/flash_messages/component.py) drains pending messages off the request, maps Django's level tags (`success` / `error` / `warning` / `info`) to the shared `alert` component's variants, and is invoked once from `chrome/layout.djx` so both the admin chrome and the auth chrome surface flashes without repeating HTML. It lives in the shared kit, so the shortener and feature-flags banners answer to the same level mapping.

### 10. Delete with `get_deleted_objects`

[`.../[int:pk]/delete/page.py`](shadcn_admin/pages/%5Bstr%3Aapp_label%5D/%5Bstr%3Amodel_name%5D/%5Bint%3Apk%5D/delete/page.py) calls `admin.utils.get_deleted_objects([obj], request, model_admin.admin_site)` to compute the dependency tree, protected references, and missing permissions. The confirmation template disables the submit button when the action is blocked. On confirm, `ModelAdmin.log_deletions` plus `delete_model` run and the user lands on the changelist.

### 11. History from `LogEntry`

[`.../[int:pk]/history/page.py`](shadcn_admin/pages/%5Bstr%3Aapp_label%5D/%5Bstr%3Amodel_name%5D/%5Bint%3Apk%5D/history/page.py) takes the row through `Depends("admin_object")`, which [`shadcn_admin/deps.py`](shadcn_admin/deps.py) registers on top of the `admin_spec` of section 4, so an unknown pk answers `404` and a row the user may not view answers `403` exactly as on the change view. `object_history` in [`shadcn_admin/services.py`](shadcn_admin/services.py) then reads `LogEntry.objects.filter(content_type=..., object_id=...).select_related("user")` and returns frozen `HistoryEntry` rows labelled by `action_flag` (1 Added, 2 Changed, 3 Deleted). The history belongs to the admin, as `ModelAdmin.history_view` does in Django, so the page sits next to the change and delete views and the query lives in a function of the admin app rather than in its page tree.

### 12. Access on the page tree

Every admin page asks for a signed-in staff account, and the rule sits in the page tree next to the pages it closes. The dashboard's [`shadcn_admin/pages/page.py`](shadcn_admin/pages/page.py) declares it for itself and every page below it in two lines:

```python
page.login_required(inherit=True)
page.permission_required("shadcn_admin.use_admin", inherit=True)
```

A declaration without `inherit=True` binds only the page of its own file. With it the rule passes down the tree to every page below, the way `inherit_context=True` hands a context down, and joins whatever each lower `page.py` adds. The rule binds the page, its actions and a standalone render of its zones, so every path that delivers the page answers the same question.

`shadcn_admin.use_admin` is a registered permission with no database row behind it. [`shadcn_admin/permissions.py`](shadcn_admin/permissions.py) binds it to the `is_staff` predicate through `add_perm`, so the superuser and every staff account hold it and nobody else does. An anonymous visit to any page of the tree redirects to `/admin/login/?next=<path>`, and a signed-in user without staff gets `403`, the way Django's `PermissionRequiredMixin` answers. An inherited rule reaches the pages of its own tree and stops at its edge, as a `layout.djx` does. Each of the other two trees therefore carries a `page.py` at its root that only declares. [`admin_audit/pages/page.py`](admin_audit/pages/page.py) repeats the two requirements with `inherit=True` and adds `page.permission_required("admin_audit.view_adminactivitylog", inherit=True)`, the right to read the audit log, for every page the app serves, and [`library/pages/page.py`](library/pages/page.py) repeats the two. Such a file has no template and no `render`, so it serves no URL of its own, while `/admin/` stays the dashboard of `shadcn_admin` and `manage.py routes` lists no row for either file. Its declarations still bind every page below it, and it may carry the `metadata` of its directory, the `Dashboard` crumb of section 13.

[`config/settings.py`](config/settings.py) sets `NEXT_FRAMEWORK["LOGIN_REQUIRED"] = True`, so a page, action or zone that declares nothing asks for a login as well. Listing Django's `LoginRequiredMiddleware` in `MIDDLEWARE` switches on the same behaviour, and every page tree answers the same way under either. The key reaches only what the page trees serve, so the site root redirect in [`config/urls.py`](config/urls.py), a plain Django view, stays open and `/` still leads to `/admin/` with the login link carrying `next=/admin/`. The sign-in and farewell pages have to stay open too. [`pages/login/page.py`](shadcn_admin/pages/login/page.py) and [`pages/logout/page.py`](shadcn_admin/pages/logout/page.py) call `page.public()`, which lifts the inherited rule and the login by default for their own page and marks their views `login_not_required`. Without `inherit=True` the lift stops there, so a page added under `login/` would be closed again.

An action POST goes to `/admin/_next/form/<uid>/` rather than to the page URL, so the dispatcher applies the rule of the file that declares the action and the rule of the posted origin page before the handler runs. The add, change and delete pages sit under the closed tree, so an anonymous POST from any of them redirects to the login and a non-staff POST answers `403` before any data is read. Every mutating action (`admin:add`, `admin:change`, `admin:delete`, `admin:bulk_action`) also registers with `@action(..., login_required=True)` and re-checks the matching `ModelAdmin` permission before it writes (`has_add_permission` when `AdminFormSpec.resolve` builds the add spec, `has_change_permission`, `has_delete_permission` and `has_view_or_change_permission` inside the handlers), raising `PermissionDenied` for a staff account without the model right. The custom bulk action `mark_as_published` additionally declares `permissions=["change"]`, so `ModelAdmin.response_action` filters it out of `get_actions(request)` for users who cannot change books. `admin:login` is declared in a public page and stays open. `admin:logout` lives in `shadcn_admin/forms.py`, outside any page tree, and declares nothing, so `LOGIN_REQUIRED` asks it for a login unless the page it was posted from is public.

`uv run python manage.py permissions` prints the rule of every endpoint and the files it comes from, and `manage.py permissions /admin/activity/` narrows the table to one page and what it renders. The table reads what each file declares, so an action of `shadcn_admin/forms.py` shows only its own `login_required=True`, and the origin page adds its rule at dispatch. Every action names the file that declares it, and the `view` rows at the end cover what the URLconf serves beside the pages, the site root redirect, which declares nothing and stays open, the CSRF endpoint marked `login_not_required`, and the form endpoint, whose rule is that of each action:

```text
Kind    Endpoint                                                                                    Access                                                                        Sources
page    /admin/                                                                                     login, perm shadcn_admin.use_admin                                            shadcn_admin/pages/page.py
page    /admin/logout/                                                                              public                                                                        public (shadcn_admin/pages/logout/page.py)
page    /admin/login/                                                                               public                                                                        public (shadcn_admin/pages/login/page.py)
page    /admin/<str:app_label>/<str:model_name>/                                                    login, perm shadcn_admin.use_admin                                            shadcn_admin/pages/page.py
page    /admin/<str:app_label>/<str:model_name>/add/                                                login, perm shadcn_admin.use_admin                                            shadcn_admin/pages/page.py
page    /admin/<str:app_label>/<str:model_name>/<int:pk>/history/                                   login, perm shadcn_admin.use_admin                                            shadcn_admin/pages/page.py
page    /admin/<str:app_label>/<str:model_name>/<int:pk>/change/                                    login, perm shadcn_admin.use_admin                                            shadcn_admin/pages/page.py
page    /admin/<str:app_label>/<str:model_name>/<int:pk>/delete/                                    login, perm shadcn_admin.use_admin                                            shadcn_admin/pages/page.py
page    /admin/library/book/<int:book>/manuscript/                                                  login, perm shadcn_admin.use_admin, perm library.change_book on library.Book  library/pages/page.py, library/pages/library/book/[int:book]/manuscript/page.py
page    /admin/library/book/<int:book>/outline/                                                     login, perm shadcn_admin.use_admin                                            library/pages/page.py
page    /admin/activity/                                                                            login, perm shadcn_admin.use_admin, perm admin_audit.view_adminactivitylog    admin_audit/pages/page.py
action  admin:add (shadcn_admin/forms.py)                                                           login                                                                         shadcn_admin/forms.py
action  admin:change (shadcn_admin/forms.py)                                                        login                                                                         shadcn_admin/forms.py
action  admin:inline_change (shadcn_admin/forms.py)                                                 login                                                                         shadcn_admin/forms.py
action  admin:inline_add (shadcn_admin/forms.py)                                                    login                                                                         shadcn_admin/forms.py
action  admin:discard (shadcn_admin/forms.py)                                                       login                                                                         shadcn_admin/forms.py
action  admin:logout (shadcn_admin/forms.py)                                                        login (default)                                                               LOGIN_REQUIRED
action  admin:login (shadcn_admin/pages/login/page.py)                                              public                                                                        public (shadcn_admin/pages/login/page.py)
action  admin:bulk_action (shadcn_admin/pages/[str:app_label]/[str:model_name]/page.py)             login, perm shadcn_admin.use_admin                                            shadcn_admin/pages/page.py, shadcn_admin/pages/[str:app_label]/[str:model_name]/page.py
action  admin:delete (shadcn_admin/pages/[str:app_label]/[str:model_name]/[int:pk]/delete/page.py)  login, perm shadcn_admin.use_admin                                            shadcn_admin/pages/page.py, shadcn_admin/pages/[str:app_label]/[str:model_name]/[int:pk]/delete/page.py
action  add_chapter_form (library/pages/library/book/[int:book]/manuscript/page.py)                 login, perm shadcn_admin.use_admin, perm library.change_book on library.Book  library/pages/page.py, library/pages/library/book/[int:book]/manuscript/page.py
view    /                                                                                           -                                                                             -
view    /admin/_next/csrf/                                                                          public                                                                        public (next.csrf.csrf_view)
view    /admin/_next/form/<str:uid>/                                                                per action                                                                    see the action rows
```

**Plug-in points instead of a cycle.** The admin knows nothing about the apps that plug into it. [`shadcn_admin/services.py`](shadcn_admin/services.py) keeps two lists of functions, filled through `register_nav` and `register_panel` the way `admin.site.register` fills the model registry, and `admin_nav(user)` and `dashboard_panels(user)` call every registered function and gather what it returns, `NavLink` rows for the sidebar and `Panel` values for the dashboard. The `admin_shell` context processor hands the links to the `admin_sidebar` component, which groups them under their section, and the dashboard renders every panel through its own `dashboard_panel` component inside one polling zone:

```djx
{% zone "dashboard-panels" poll="5s" %}
  {% for panel in dashboard_panels %}
    {% component "dashboard_panel" panel=panel %}
  {% endfor %}
{% endzone %}
```

[`admin_audit/admin.py`](admin_audit/admin.py) registers one function of each kind. Django's admin autodiscovery imports the `admin.py` of every installed app at startup, so the registration needs no `ready()` code. A user who holds `admin_audit.view_adminactivitylog` gets an Activity feed link under Auditing and a Recent activity panel with the five newest entries of `AdminActivityLog.objects.visible_to(user).recent(5)`, and anyone else gets nothing. The audit app owns its rule and its data, and the admin renders whatever the installed apps hand it, so dropping `admin_audit` from `INSTALLED_APPS` leaves a working dashboard without the panel.

The superuser sees the five newest admin actions, and an action logged in another tab shows up on the next poll without a reload. The editor sees neither the link nor the panel. The zone stays on the page for every user and the decision lives inside it, in the registered function, because a zone under `{% has_perm %}` or `{% if %}` cannot render on its own and `manage.py check` reports it as `next.E161`. The editor's poll and a hand-made `dashboard-panels` request answer an empty zone. The rows never leave the function, and an anonymous request is sent to the login like any other.

**Why no `obj=` on the generic pages.** `permission_required(perm, obj=DObject[Model])` checks a fixed permission name against a row of a fixed model. Every generic admin page serves whichever model `app_label` and `model_name` name in its URL, so the permission and the model move with the URL and no static declaration fits them. The row rights of section 14 go through `ModelAdmin.has_change_permission(request, obj)` there. A tree that serves one model can name both, and the book manuscript of section 15 does.

The integration suite pins each answer for an anonymous visitor, a non-staff account, the editor and the superuser, and [`tests/e2e/test_permissions.py`](tests/e2e/test_permissions.py) drives them in a browser, from the login redirect and the refused anonymous POST and zone request to the editor's dashboard without the panel and the superuser's feed picking up a new entry on its next poll.

Login runs Django's own `AuthenticationForm`, which the dispatcher cannot build directly: its `__init__(self, request, data=...)` takes the request positionally, while dispatch calls `form_class(post_data, files, ...)`. `admin_login_form_factory` bridges the gap by returning the tuple `(AuthenticationForm, {"request": request})`, so the dispatcher constructs the form with `data=` plus that keyword and the handler only calls `form.get_user()`. Bad credentials fail the form's own validation before the handler runs, and the login page re-renders with the non-field error [`pages/login/template.djx`](shadcn_admin/pages/login/template.djx) already prints. The `login_state` context supplies the `?next=` target the template writes into a hidden field, which `admin_login` reads back from `request.POST` to pick the post-login destination.

### 13. A private site in the head

Every tag the admin puts in `<head>` comes from `NEXT_FRAMEWORK["SITE"]` and `NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]` in [`config/settings.py`](config/settings.py):

```python
"SITE": {
    "URL": "https://admin.example",
    "NAME": "next.dj admin",
    "INDEXABLE": False,
},
"METADATA": {
    "DEFAULTS": {
        "viewport": "width=device-width, initial-scale=1",
        "icons": {"icon": {"url": "/static/shadcn_admin/icon.svg", "type": "image/svg+xml"}},
        "title": {"template": "{title} · {site_name}", "default": "next.dj admin"},
    }
},
```

`SITE["NAME"]` fills `{site_name}` and `SITE["URL"]` is the origin the icon link is absolute on. `SITE["INDEXABLE"] = False` closes the whole site in one place, whatever `DEBUG` says and whatever a page declares. `check --deploy` reports nothing about it here. Its warning about a closed site fires only when the site also publishes a sitemap or a robots file, the sign of a staging setting leaking into production, and the admin publishes none.

[`chrome/layout.djx`](chrome/layout.djx) calls the shared `page_head` component without a title, and the component renders the builtin `{% metadata %}` tag. The site rule sits over every fold, so every page, the login screen included, carries `<meta name="robots" content="noindex, nofollow">`, and every response carries the same `X-Robots-Tag` header. `nofollow` matters on the one page a crawler can reach. The rules of section 12 send every anonymous request under `/admin/` to `/admin/login/`, and the tag stops a crawler that arrives there from following its links any further.

The one dynamic tier is `changelist_meta` in [`[str:model_name]/page.py`](shadcn_admin/pages/%5Bstr%3Aapp_label%5D/%5Bstr%3Amodel_name%5D/page.py):

```python
@page.metadata(inherit=True)
def changelist_meta(app_label: str, model_name: str) -> MetadataDict:
    model, _ = utils.resolve_model_admin(app_label, model_name)
    return {"title": capfirst(str(model._meta.verbose_name_plural))}
```

`inherit=True` extends it to every page below the changelist in the same tree, the add and change views that exist only as `template.djx` directories and the delete and history pages, so all five tabs read `Books · next.dj admin`. The callable takes the URL kwargs rather than the `changelist_state` context, because that context is page-local and absent on the sub-pages, and the lookup of section 2 reads the app registry without a query.

The shell renders the trail through `{% breadcrumbs as crumbs %}`, so the crumbs are page metadata too. [`shadcn_admin/pages/page.py`](shadcn_admin/pages/page.py) declares `{"breadcrumb": "Dashboard"}`, the changelist labels its crumb with its own title, and every crumb reverses with the URL kwargs of the current match, so a delete page reads `Dashboard › Books` with both links filled. The history page declares `History` and reads `Dashboard › Books › History`, its own crumb reversed with the `app_label`, `model_name` and `pk` of the match. A trail is read from the `page.py` files of one tree, from its root down to the page, and stops at the edge of that tree like a layout and an inherited context. The root `page.py` of `admin_audit` and of `library` (section 12) declares `{"breadcrumb": "Dashboard"}` for the directory it sits in, and a crumb reverses the page of its directory in the namespace of the request, so the `""` trail of either tree links to `/admin/`, which `shadcn_admin` serves under the same include. The activity feed therefore reads `Dashboard › Activity` and the book outline `Dashboard › Outline`.

Neither page root declares a `sitemap.py` or a robots source, so the framework routes neither and `/robots.txt` and `/sitemap.xml` answer 404. A sitemap hands crawlers the URLs worth indexing, and every URL here sits behind a login and says `noindex`. A robots file would be the wrong tool as well. `Disallow: /admin/` stops the fetch, a crawler that never fetches a page never reads its `noindex`, and a URL linked from elsewhere can still be listed as a bare address. The tag needs the fetch, so the admin leaves robots out and lets each page say `noindex, nofollow` itself.

### 14. Object permissions for an editor

The stock `ModelAdmin` asks `request.user.has_perm("library.change_book")` and leaves the row out, so Django's `ModelBackend` can only say yes or no for the whole table. [`library/permissions.py`](library/permissions.py) answers per row instead. It declares plain functions of the user, or of the user and the row, and binds them to the default permission names of the `library` models:

```python
def holds_draft(user: Visitor, book: Book) -> bool:
    return book.status == Book.DRAFT and book.editor_id in {None, user.pk}


def edits_book(user: Visitor, book: Book | None) -> bool:
    return uses_admin(user) and (book is None or holds_draft(user, book))


add_perm("library.change_book", edits_book)
add_perm("library.delete_book", edits_book)
add_perm("library.change_chapter", edits_chapter)
```

`uses_admin(user)` reads only the user, and it asks `user.has_perm("shadcn_admin.use_admin")`, the right of section 12, so the library follows whatever rule the admin registers for it without importing the admin app. `edits_book(user, book)`, `edits_chapter(user, chapter)` and `deletes_tag(user, tag)` take the row second and join the two questions with plain `and` and `or`. A `None` row is the model-level question, where Django passes no row and asks whether the user may change books at all, which lets the changelist and the dashboard open for the editor while every saved row still asks for a draft. Authors and tags are bound to `uses_admin` alone, and `add_*` too, so the editor reaches the whole `library` app and nothing outside it. The framework imports `permissions.py` at startup, and [`config/settings.py`](config/settings.py) lists the backend that asks the predicates next to the default one:

```python
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "next.permissions.ObjectPermissionBackend",
]
```

**The policy.** An editor edits drafts and nothing past them. Every book carries an optional `editor`, a foreign key to the account that works it, and a draft belongs to that account, or to every editor while nobody is assigned. A book and its chapters are the editor's while the book is a draft of theirs, and a tag is anyone's to delete until a book past draft carries it. The editor may not publish, archive, or demote a book, through any route, while a superuser may do all of it.

**Where the right is checked.** [`library/admin.py`](library/admin.py) gives every library admin the `RowPermissionAdmin` base from [`shadcn_admin/admin.py`](shadcn_admin/admin.py), whose `has_change_permission(request, obj)` and `has_delete_permission(request, obj)` ask `request.user.has_perm(perm, obj)` with the row. Every check reads the row as it was stored, before the posted data binds, because `AdminFormSpec` keeps a `stored` copy next to the `instance` the form fills. `handle_change` asks with that copy, so a published book posted back as a draft still answers `403`. `RowPermissionAdmin.get_form` adds the other half: on a row the user may edit, the form refuses a save whose resulting row the user could no longer change, so publishing a draft, adding a book as published, or adding or moving a chapter into a published book re-renders with "You may not save this book in this state." `ChapterAdmin` offers only the books the user may change as a chapter's book. `ChapterInline` takes the `ParentRowPermissions` mixin from the same module, so its hooks ask `library.change_book` against the parent book, which locks the live inline forms of a published book. `mark_as_published` keeps only the rows the user may change both before and after the flip, which for the editor is none.

Deleting follows Django's own `delete_view`. The delete page and `admin:delete` share one `deletion()` helper over `get_deleted_objects`, and the delete runs only when every row it cascades to is one the user may delete, so an author with a published book, or a tag carried by one (`library.delete_tag` is bound to `deletes_tag`), stays for the editor.

Reading is narrowed as well. `resolve_object_or_404` and the changelist answer `403` unless `has_view_or_change_permission` holds, and the add view `403` unless `has_add_permission` does, so the editor reaches no `auth` page, and the sidebar shows the activity feed only to a user the nav function of `admin_audit` finds holding `admin_audit.view_adminactivitylog`. The generic `admin:change` action serves every model through one factory, so a static `Meta.permission_required` cannot name a per-model permission there, and `ModelAdmin` is where the right honestly lives.

**What the editor sees.** The change view of [`admin_form`](shadcn_admin/_components/admin_form/component.djx) wraps the `save_bar` component and the Delete link in `{% has_perm %}`, naming the permission through `form_state.change_perm` and `form_state.delete_perm`. `form_state.row_rules` says whether the model's admin is a `RowPermissionAdmin`, and only then does the tag pass the row through `form_state.instance`, so it asks exactly the question the admin's own hook asks. Every other model, such as `auth.Group`, asks the model-level question, which `ModelBackend` answers from group and user permission rows, because `ModelBackend` answers nothing once a row is named. The inlines follow the same split. Each live section carries `InlineRights`, which name the parent's `change` permission and pass the parent row for a `ParentRowPermissions` inline and ask the child's model-level `change` and `add` otherwise. The `inline_row` component renders `inline_row_form` or the read-only `inline_row_view` under that tag, and `inline_live` renders the add form only when the add permission holds, so the editor sees a published book's chapters as plain values with no Save and no add form. A published book renders a read-only note in place of the buttons for the editor and the full form bar for a superuser, who holds every permission before any backend is asked. The tag sits inside the `record` zone of the change page, never around it, so `manage.py check` stays clean.

Sign in as `editor` / `editor` to see the difference: `/admin/library/book/` lists every book with its editor, a draft of theirs such as `Mathilda` saves and deletes, while `The Mysterious Island`, a draft of `copyeditor`, `Frankenstein` and any other published or archived book open read-only. Sign in as `copyeditor` / `copyeditor` and the two drafts swap places. The unit suite pins each predicate for both editors, a non-staff user, an anonymous user and `None`, the integration suite drives the same rows through `user.has_perm` and the `403` and `302` answers under both accounts, and `tests/e2e/test_permissions.py` signs the editor in through `/admin/login/`.

### 15. One book behind `obj=`

The generic pages serve any model, so the pages that know they serve a book live in the tree of the `library` app, under the directories that spell the URL of one book, `library/pages/library/book/[int:book]/`. The tree holds two pages. [`outline/page.py`](library/pages/library/book/%5Bint%3Abook%5D/outline/page.py) at `/admin/library/book/<book>/outline/` shows the structure of the book, its chapters with their word count, to every admin. [`manuscript/page.py`](library/pages/library/book/%5Bint%3Abook%5D/manuscript/page.py) at `/admin/library/book/<book>/manuscript/` is where chapters are appended, and only the editor of the draft works there. The generic routes under the same URL end in `change/`, `delete/` and `history/`, so neither claims them.

Two `page.py` files split the rule along the tree. [`library/pages/page.py`](library/pages/page.py) at the root of the tree declares the admin rule with `inherit=True` and serves no URL (section 12), since an inherited rule stops at the edge of its tree, and binds both pages below. [`manuscript/page.py`](library/pages/library/book/%5Bint%3Abook%5D/manuscript/page.py) adds the row for its own page in one line:

```python
page.permission_required("library.change_book", obj=DObject[Book])
```

`obj=DObject[Book]` reads the pk out of the URL kwarg named after the model, which is why the directory captures `[int:book]`, and `manage.py check` reports a route without that kwarg as `next.E162`. Once the login and `use_admin` hold, the framework loads the book and asks `request.user.has_perm("library.change_book", book)`, the row question `RowPermissionAdmin.has_change_permission` asks on the change view, so `edits_book` from section 14 decides. The rule binds the page, the actions declared beside it and a standalone render of its zone, so the editor on a draft of `copyeditor` or on a published book gets `403` on the GET, on a hand-made `add_chapter_form` POST and on a `manuscript` zone request alike, before any context runs.

The row loads through `Book.objects.visible_to(scope)`, as every `DObject` does. [`library/deps.py`](library/deps.py) registers `scope` as the visitor, and [`library/models.py`](library/models.py) gives `Book` a queryset whose `visible_to` returns every book to staff and none to anyone else. The visibility is deliberately that coarse. A scope that hid foreign or published books from the editor would turn them into `404`, while they exist and open read-only, so the scope only says who works the admin and the row right stays with `library.change_book`. A non-staff account never reaches the load, since `use_admin` refuses it first with `403`, and an unknown pk answers `404` at the load.

Both pages give the template their book with `@context("book")` over a `book: DObject[Book]` parameter and their chapters with a zone-bound context, and both render the table through one `chapter_table` component in [`[int:book]/_components/`](library/pages/library/book/%5Bint%3Abook%5D/_components/chapter_table/component.djx). Every `DObject` read of one request shares the cache entry the rule filled, so the GET of the manuscript reads `library_book` once, the check included. The form lives on the manuscript alone. The outline decides inside its zone whether to offer the way there, in [`outline/template.djx`](library/pages/library/book/%5Bint%3Abook%5D/outline/template.djx):

```djx
{% zone "outline" %}
  {% component "chapter_table" chapters=outline.chapters words=outline.words %}
  {% has_perm "library.change_book" book %}
    {% component "button" href=manuscript_url text="Open the manuscript" %}
  {% else %}
    <p data-outline-read-only>The book is not a draft of yours, so its outline is read-only.</p>
  {% endhas_perm %}
{% endzone %}
```

A zone request renders the zone on its own, so the tag inside it asks again for the user of every request. The reverse nesting, a zone under `{% has_perm %}`, would hand a partial request a zone whose decision never ran, and `manage.py check` reports it as `next.E161`. The link and the rule ask the same question, so the outline never offers a manuscript that answers `403`.

`AddChapterForm` is a `ModelForm` declared in the manuscript page, so its action carries the rule of the page. Its `get_form_kwargs` binds a new chapter of the book, `{"instance": Chapter(book=book)}`, and `on_valid` hands it to `services.append_chapter` in [`library/services.py`](library/services.py), which numbers it one past the end through `chapter.book.chapters.next_number()`, then morphs the `manuscript` zone, and without the runtime it answers `303` back to the manuscript.

**What the editors and the superuser see.** The change view of every book shows an Outline button, which `BookAdmin.get_object_links` returns and `admin_form` renders, while a `RowPermissionAdmin` returns no links by default. The editor opens the outline of `Mathilda`, follows Open the manuscript, appends a chapter, and watches the table grow without a reload. On `The Mysterious Island` or any published or archived book the editor reads the outline with the read-only note in its place, and the manuscript answers `403`. The superuser holds every permission before any backend is asked, so every book opens its manuscript and takes new chapters. An anonymous visit is sent to `/admin/login/?next=<path>`.

The trails read `Dashboard › Outline` and `Dashboard › Manuscript`, the crumbs of the `library` tree, whose root lends the `Dashboard` (section 13). The outline links back to the change view and the manuscript back to the outline through their own buttons.

`manage.py permissions /admin/library/book/1/manuscript/` narrows the table to the page and its action, each with `perm library.change_book on library.Book` from `manuscript/page.py`, while the outline row carries only the admin rule of `library/pages/page.py`. The integration suite drives the manuscript page, its zone through `NextClient.get_zones` and its action under the editor on an own, an unassigned and a foreign draft and a published book, under the superuser and anonymously, and renders the outline zone for each case to pin where the link shows. `tests/e2e/test_permissions.py` has the editor reading a published book and a foreign draft without the link and refused on every manuscript path, the editor appending a chapter to an own draft under its trail, and the superuser appending one to a published book.

## What is in core for this example

Seven pieces of next.dj carry the example.

- **Access on the page tree.** [`next/pages/access.py`](../../next/pages/access.py) joins what the `page.py` files above a page declare with `inherit=True`, from the tree root down, with what its own `page.py` declares, where a `public()` lifts what sits above it and the login by default, and loads the row an `obj=DObject[Model]` names through `visible_to(scope)` before it asks, [`next/forms/dispatch/permissions.py`](../../next/forms/dispatch/permissions.py) applies the same rule to an action POST. `manage.py permissions` reads the result back.
- **`@action(form_class=callable)`.** [`next/forms/dispatch/build.py`](../../next/forms/dispatch/build.py) accepts a callable beside a `Form` subclass. `_resolve_form_class` runs the factory through `resolver.resolve_dependencies` once per request, so admin pages produce a class shaped by `ModelAdmin.get_form()` on the fly. A factory may also return a `(form_class, init_kwargs)` tuple. That branch skips `get_initial` and forwards the extra kwargs to the constructor, which is how the login action drives a form whose signature the dispatcher could not otherwise satisfy (see section 12).
- **`{% form "action_name" %}`.** The tag takes a positional action name (a string literal or a context variable) plus optional `key="value"` HTML attributes rendered onto the opening `<form>` tag. `action`, `method`, and the `data-next-*` prefix are reserved for the framework. [`next/templatetags/forms.py`](../../next/templatetags/forms.py) compiles the value through `parser.compile_filter`, so the `admin_form` template keeps one `{% form %}` block and resolves `admin:add` or `admin:change` from `form_state.action_name` at render time, while [`action_bar`](shadcn_admin/_components/action_bar/component.djx) passes `id=` and `class=` straight on the tag. The companion `{% action_url %}` tag resolves the dispatch URL by action name for hand-crafted forms. The topbar Sign out form in [`chrome/layout.djx`](chrome/layout.djx) uses it.
- **`{% form key=... %}` distinguishes repeated forms.** A form rendered once per row shares one action UID, so `key=` writes `data-next-key` and the client morphs the submitted instance rather than the first. The change view's per-row `admin:inline_change` forms key on the child primary key. The `next.W070` system check flags a looped `{% form %}` that carries neither a `key=` nor a wrapping `zone=`.
- **`action_dispatched` carries `form` and `url_kwargs`.** [`next/forms/dispatch/`](../../next/forms/dispatch/) sends both fields on every successful dispatch (the form is `None` for handlers without a `form_class`). [`admin_audit.signals.log_admin_action`](admin_audit/signals.py) reads them to write one `AdminActivityLog` row per dispatch without ever touching the handlers it observes.
- **`Form.clean()` → `ValidationError` re-renders the origin page.** The same path the framework already uses for field-level errors lets `AdminForm.clean()` validate inline formsets and surface their errors on the bound form, with the user's typed data preserved.
- **Virtual pages survive form re-render.** The dispatcher resolves the posted `_next_form_origin` URL against the URLconf and reads the page identity from the matched view, so a page that exists only as a `template.djx` directory (no `page.py` anchor) re-renders correctly on validation failure. That lets the add and change views in this example live as `template.djx`-only directories.

## Further reading

- [`next/forms/decorators.py`](../../next/forms/decorators.py) and [`next/forms/dispatch/`](../../next/forms/dispatch/) for the `@action` factory contract.
- [`next/forms/signals.py`](../../next/forms/signals.py) for the `action_dispatched` / `form_validation_failed` payload contracts.
- [`next/templatetags/forms.py`](../../next/templatetags/forms.py) for `{% form %}` argument compilation.
- [`docs/content/topics/forms/actions.rst`](../../docs/content/topics/forms/actions.rst) for the form-action lifecycle, including the guards and the factory pattern used by sections 4 and 5 above.
- [`docs/content/topics/layouts.rst`](../../docs/content/topics/layouts.rst) and [`docs/content/topics/pages.rst`](../../docs/content/topics/pages.rst) for the project-level layout shown in section 1.
- [`docs/content/topics/components.rst`](../../docs/content/topics/components.rst) for the component lookup configured under `_components/`.
- [`docs/content/topics/context.rst`](../../docs/content/topics/context.rst) for the `@context` callables the pages declare and the context processors the router runs.
- [`docs/content/topics/dependency-injection.rst`](../../docs/content/topics/dependency-injection.rst) for `@resolver.dependency` and `Depends("name")` used by the `admin_spec` registration in section 4.
- [`docs/content/topics/file-router.rst`](../../docs/content/topics/file-router.rst) for the `[str:app_label]/[int:pk]/` directory naming under `pages/`.
- [`next/pages/metadata/`](../../next/pages/metadata/) for the metadata chain behind the settings tier and `@page.metadata(inherit=True)` in section 13.
- [`docs/content/topics/permissions.rst`](../../docs/content/topics/permissions.rst) for the predicates, the backend and the `{% has_perm %}` tag of section 14.
