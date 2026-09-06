# URL shortener

A bit.ly-style mini-product built on **next-dj**. Paste a long URL, get a short slug, share it. Every visit to `/s/<slug>/` is counted in `LocMemCache`, and a management command flushes the counters into SQLite.

This is the example to read first. It is the smallest complete project in the catalog and doubles as a walkthrough of the surface every other example builds on: the file router, layouts, context functions, forms, components, patch envelopes, a custom DI provider, URL reversing, active-link highlighting, and a cache-backed hot path.

## What you will see

| URL | Description |
| --- | --- |
| `/` | Form to shorten a URL, badge with the unflushed click total, list of the latest five entries. |
| `/s/<slug>/` | 302 redirect to the original URL, bumps the click counter. |
| `/admin/` | Top ten links with an inline edit form and a delete button per row, plus the unflushed click counters. Nested admin layout with a subnav. |
| `/admin/stats/` | Totals for links, persisted clicks, and pending clicks, plus the live per-action dispatch counters. |
| `/admin/links/<slug>/` | Link detail, resolved through a custom `DLink[Link]` DI provider. Resets the cached clicks for that slug. |

## How to run

```bash
cd examples/shortener
uv run python manage.py migrate
uv run python manage.py runserver     # http://127.0.0.1:8000/
uv run pytest
```

Tailwind loads via the Play CDN in the shared [`page_head`](../_shared/_components/page_head/component.djx) component that [`host/layout.djx`](host/layout.djx) pulls in. No Node, no build step.

## Walking the code

### 1. Two page roots, and directory names you pick

[`config/settings.py`](config/settings.py) configures both backends under `NEXT_FRAMEWORK`:

```python
NEXT_FRAMEWORK = {
    "PAGE_BACKENDS": [
        {
            "BACKEND": "next.urls.FileRouterBackend",
            "APP_DIRS": True,
            "DIRS": [str(BASE_DIR / "host")],
            "PAGES_DIR": "routes",
            "OPTIONS": {"context_processors": []},
        }
    ],
    "COMPONENT_BACKENDS": [
        {
            "BACKEND": "next.components.FileComponentsBackend",
            "DIRS": [str(SHARED_DIR / "_components")],
            "COMPONENTS_DIR": "_widgets",
        }
    ],
}
```

The router walks two roots. `DIRS` names the project-level page root [`host/`](host/), which owns the single outermost `layout.djx`. `APP_DIRS = True` adds the `routes/` tree of every installed app, so the pages themselves live in [`shortener/routes/`](shortener/routes/). Component lookup is layered the same way: the shared shadcn kit in [`../_shared/_components/`](../_shared/_components/) through `DIRS`, plus any `_widgets/` folder the page walk meets inside a page tree.

The framework hardcodes neither name. `routes/` could be `screens/` or `panels/`, `_widgets/` could be `_cards/` — the other examples rename both on purpose.

### 2. Page, template, layout — how a URL is rendered

A directory under `routes/` with a `page.py` becomes a URL. The framework composes a template in three layers:

- **`layout.djx`** (any ancestor directory) — the outer shell. Must contain an empty placeholder `{% block template %}{% endblock template %}` where the child content is substituted.
- **`template.djx`** (sibling of `page.py`) — the page body. Just HTML. No `{% block template %}` wrapping needed because the framework handles substitution.
- **`page.py`** — Python side: context functions (`@context`), optional self-registering form classes (`next.forms.Form`/`ModelForm`), optional `template = "..."` module attribute, optional `render(request, ...) -> HttpResponse`.

Ancestor layouts cascade: `routes/admin/stats/` inherits `routes/admin/layout.djx`, which itself is wrapped by [`host/layout.djx`](host/layout.djx). Look at the nested toolbar in [`admin/layout.djx`](shortener/routes/admin/layout.djx):

```djx
<div class="space-y-6">
  {% #component "page_header" title="Admin panel" %}
    {% #slot "actions" %}
      {% #component "nav" %}
        {% #slot "content" %}…subnav…{% /slot %}
      {% /component %}
    {% /slot %}
  {% /component %}

  {% block template %}{% endblock template %}
</div>
```

The placeholder is empty. The outer toolbar stays visible on every admin sub-page.

### 3. Context functions — feeding data to the template

Three patterns, each useful:

**Keyed single value** — the most common:

```python
@context("recent_links")
def recent_links() -> list[Link]:
    return list(Link.objects.all()[:5])
```

Renders as `{{ recent_links }}` in the template.

**Unkeyed dict** — group related values, avoid duplicate DI resolution:

```python
# routes/admin/links/[slug]/page.py
@context
def link_context(link: DLink[Link]) -> dict[str, object]:
    return {"link": link, "cache_key": f"{CLICK_PREFIX}{link.slug}"}
```

`@context("link")` + `@context("cache_key")` would each trigger the `DLink` provider and hit the database twice. The unkeyed form runs the dependency once, merges the dict into the template context.

**Reusing a shared helper** — wrap it in the page module that needs it:

```python
# routes/admin/page.py
from shortener.cache import pending_clicks


@context("pending_clicks")
def admin_pending_clicks() -> dict[str, int]:
    return pending_clicks()
```

`@context` keys the registration on the file where the decorated function is declared, so decorating `pending_clicks` in place would bind it to `shortener/cache.py` instead of to this page.

### 4. `inherit_context=True` — sharing context down the tree

```python
@context("recent_links", inherit_context=True)
def recent_links() -> list[Link]:
    return list(Link.objects.order_by("-clicks", "-created_at")[:10])
```

Declared once in [`admin/page.py`](shortener/routes/admin/page.py), available in `admin/stats/` and `admin/links/<slug>/` templates. Use it for toolbar-level data. Do not mark heavy queries `inherit_context=True` unless every sub-page actually needs them.

### 5. Forms — class-bound `Form` + `{% form %}`

[`routes/page.py`](shortener/routes/page.py) declares the form class. A `next.forms.Form` subclass registers itself by file path through `__init_subclass__`, so its auto-name is `create_link_form` (snake_case of the class). The submit logic lives in `on_valid`, no separate handler. The redirect target and the flash message are declared on `Meta`:

```python
class CreateLinkForm(Form):
    url = forms.URLField(
        max_length=2000,
        assume_scheme="https",
        widget=ComponentWidget(
            "input", type="url", placeholder="https://example.com/very/long/path"
        ),
    )

    class Meta:
        success_url = "/"
        success_message = "Short link created for %(url)s."
```

`on_valid` receives only the parameters it declares — the DI resolver fills what the signature asks for. Delegating to `super().on_valid(request)` follows `Meta.success_url`, and the dispatcher flashes `Meta.success_message` (interpolated over `cleaned_data` with `%` formatting) through `django.contrib.messages`. The home page drains the queue in a `flash_messages` context callable and renders each entry through the shared `alert` component. `ComponentWidget("input", type="url", ...)` makes `{{ form.url }}` render through the shared `input` component instead of Django's default widget, so a form field and a hand-written control look identical.

Creating the row is its own problem: [`_create_link_with_unique_slug`](shortener/routes/page.py) tries random six-character slugs inside `transaction.atomic()` and catches `IntegrityError` from the unique constraint, widening the slug by one character every ten collisions. The database decides uniqueness, so two concurrent submissions cannot both win a slug.

[`routes/template.djx`](shortener/routes/template.djx) renders the form by its auto-name:

```djx
{% form "create_link_form" %}
  {% #component "field" label="Long URL" for_id="id_url" %}
    {% #slot "control" %}{{ form.url }}{% /slot %}
  {% /component %}
  {% if form.errors %}
    {% component "alert" variant="destructive" text=form.url.errors|first %}
  {% endif %}
  {% component "button" type="submit" text="Shorten" variant="default" %}
{% endform %}
```

The `{% form "name" %}` tag resolves the form to its stable UID endpoint, injects a CSRF token, and exposes the bound `form` in the block.

> `{% form %}`, `{% component %}`, `{% collect_styles %}`, `{% url %}` etc. are all globally loaded template tags. **Do not** write `{% load forms components next_static %}` — `next.apps.templates.install()` registers them as Django builtins at startup.

The admin list edits each link inline. The same form renders once per row, so each carries `key=link.slug` (`data-next-key`) and an invalid submit re-renders the submitted row, not the first:

```djx
{% form "edit_link_form" key=link.slug %}
  <input type="hidden" name="slug" value="{{ link.slug }}">
  <input name="url" value="{{ link.url }}" type="url">
  {% if form.url.errors %}<span>{{ form.url.errors|join:", " }}</span>{% endif %}
  <button type="submit">Save</button>
{% endform %}
```

[`EditLinkForm`](shortener/routes/admin/page.py) is a `ModelForm` over `Link` and resolves the edited row from the posted `slug` in `get_initial`, so one registered form serves every row. A looped `{% form %}` without `key=` or `zone=` raises `next.W070`.

### 6. Patch envelopes — `prepend`, `remove`, and an out-of-band foreign morph

Three actions author their own patch envelopes through `Patches(request)` and fall back to a redirect when no runtime is present, so each works the same with or without JS.

- **`prepend` with dedupe.** The home create form wraps its latest-links list in a `{% zone "latest-links" tag="ul" %}`. On a partial submit [`CreateLinkForm.on_valid`](shortener/routes/page.py) renders one keyed `link_row` and prepends it: `Patches(request).prepend({"zone": "latest-links"}, row, dedupe="key").response()`. Dedupe by `data-next-key` (the slug) means a resubmission replaces its row instead of doubling it. Without the runtime the form keeps its declared `Meta.success_url` redirect. The empty-state branch lives _inside_ the zone body rather than around the tag, because a standalone zone render never evaluates an enclosing `{% if %}` — the framework rejects the other arrangement with `next.E063`.
- **`remove`.** Each admin row is a `<li data-next-key="{{ link.slug }}">`. The [`delete_link`](shortener/routes/admin/page.py) action drops the row in place: `Patches(request).remove({"css": 'li[data-next-key="..."]'}).response(fallback="/admin/")`.
- **`morph_foreign_zone` (out of band).** The home page owns a `{% zone "links-badge" %}` that totals unflushed clicks. Both home-page providers are bound to their own zone — `@context("pending_total_label", zone="links-badge")` and `@context("recent_links", zone="latest-links")` — so the badge morph never lists the links and a `latest-links` render never totals the click cache, while the full page render still runs both. The [`reset_clicks`](shortener/routes/admin/links/[slug]/page.py) action on the detail page re-renders that zone of the _foreign_ home page out of band: `Patches(request).morph_foreign_zone("links-badge", "/")`. The home page's body resolution re-runs first, so the zone travels only when that page would have served the request.

The row markup ships from one place. `on_valid` renders the same [`link_row`](shortener/routes/_widgets/link_row/component.djx) component the page render uses, passing the page's `template.djx` path as `current_template_path` so the component resolver finds a page-scoped `_widgets/` name outside a page render.

Every partial response carries the asset version so the client can tell a stale tab from a fresh deploy. These examples serve assets straight off disk with no hashed manifest to derive a version from, so `config/settings.py` pins one explicitly with `extend_default_backend("PARTIAL_BACKENDS", OPTIONS={"VERSION": "v1"})`. The default `"manifest"` sentinel would resolve to a constant here and leave the guard silent.

### 7. Components — simple, composite, and shared

A component lives in `_widgets/<name>/`:

- **Simple**: just `component.djx` — pure template.
- **Composite**: `component.py` + `component.djx`. The Python side adds context via `@component.context("key")`.

[`_widgets/link_card/`](shortener/routes/_widgets/link_card/) is a composite. The template renders a card while the Python function computes a display URL via `reverse`:

```python
@component.context("short_url")
def short_url(link: Link) -> str:
    return reverse("slug_redirect", kwargs={"slug": link.slug})
```

Usage in a loop:

```djx
{% for link in recent_links %}
  {% component "link_card" %}
{% endfor %}
```

Every `{% component %}` prop compiles as a Django `FilterExpression`, so `title="Hello"` passes a literal while `link=link` passes the loop variable. The tag also forwards the parent template's flattened context, which is why the bare call above still lands the `link` loop variable inside the component and lets `ContextByNameProvider` fill the `link: Link` parameter of `short_url`. [`link_row`](shortener/routes/_widgets/link_row/component.djx) takes the explicit route and writes `{% component "link_card" link=link %}`.

The card also ships a co-located [`component.css`](shortener/routes/_widgets/link_card/component.css). The collector emits it once per page no matter how many cards render.

### 8. Shared `nav_link` — DRY the active-state logic

Root nav and admin subnav both need the same active-state rule. The logic lives once in the shared kit at [`_shared/_components/nav_link/component.py`](../_shared/_components/nav_link/component.py), registered as a global component root through `COMPONENT_BACKENDS[0]["DIRS"]` in [`config/settings.py`](config/settings.py):

```python
@component.context("is_active")
def is_active(request: HttpRequest, url_name: str = "", active_when: str = "") -> bool:
    match = getattr(request, "resolver_match", None)
    if match is None:
        return False
    view_name = match.view_name
    if active_when:
        return active_when in view_name
    return view_name == url_name
```

Usage:

```djx
{# exact match #}
{% component "nav_link" url_name="next:page_admin_stats" label="Stats" variant="tabs" %}

{# prefix match — stays active across every URL name that contains 'page_admin' #}
{% component "nav_link" url_name="next:page_admin" active_when="page_admin" label="admin" variant="bar" %}
```

No `request.path` string-munging, no custom template tag, no context processor. Django populates `request.resolver_match.view_name` and the component reads it.

### 9. URL names and `{% url %}`

Every anchor in the project goes through `{% url %}`. File-router URLs sit under the `next` namespace. The name format is `page_<prepare_url_name(url_path)>`:

| File | URL | Name |
| --- | --- | --- |
| `routes/page.py` | `/` | `next:page_` |
| `routes/admin/page.py` | `/admin/` | `next:page_admin` |
| `routes/admin/stats/page.py` | `/admin/stats/` | `next:page_admin_stats` |
| `routes/admin/links/[slug]/page.py` | `/admin/links/<slug>/` | `next:page_admin_links_slug` |

Use them as `{% url 'next:page_admin' %}` or with args: `{% url 'next:page_admin_links_slug' slug=link.slug %}`. Rename files freely — templates stay correct because they never hardcode paths.

### 10. Custom DI provider — `DLink[Link]`

[`providers.py`](shortener/providers.py) implements a typed injection marker that fetches the matching `Link` from the URL `slug`:

```python
class DLink[T](DDependencyBase[T]):
    __slots__ = ()


class LinkProvider(RegisteredParameterProvider):
    def can_handle(self, param, _context) -> bool:
        return get_origin(param.annotation) is DLink

    def resolve(self, param, context):
        (model_cls,) = get_args(param.annotation)
        slug = context.url_kwargs["slug"]
        try:
            return model_cls.objects.get(slug=str(slug))
        except model_cls.DoesNotExist as exc:
            raise Http404 from exc
```

Two non-obvious details:

1. **Python 3.12 generic syntax is required.** `class DLink[T](DDependencyBase[T])` makes `DLink[Link]` a parameterised generic whose origin is `DLink`. Writing `class DLink(DDependencyBase[Link])` instead gives you a non-generic class and `get_origin(DLink[Link])` returns `None`.
2. **Register the provider before the resolver caches its provider list.** [`apps.py`](shortener/apps.py) imports `shortener.providers` from `AppConfig.ready()`. The `RegisteredParameterProvider.__init_subclass__` hook records the class at import time, so importing early makes the class part of the resolver snapshot.

Use it anywhere:

```python
@context("link")
def current_link(link: DLink[Link]) -> Link:
    return link
```

### 11. A plain Django view beside the file router — `/s/<slug>/`

`/s/<slug>/` answers with a redirect and never renders HTML. It could live under `routes/`: a module-level `render()` outranks the composed template, and returning any `HttpResponseBase` from it short-circuits the layout and static pipelines entirely. The example keeps the redirect as a plain view in [`views.py`](shortener/views.py) instead, to show that both routing styles share one URLconf:

```python
urlpatterns = [
    path("s/<slug:slug>/", redirect_slug, name="slug_redirect"),
    path("", include("next.urls")),
]
```

The project route comes first, so a hand-written path always wins over a page route of the same shape. Its `name="slug_redirect"` is a normal URL name, which is why the `link_card` component reverses it exactly like a file-router name.

### 12. Hot-path cache + flush command

The redirect bumps a counter in `LocMemCache` rather than SQLite:

```python
# cache.py
def increment_clicks(slug: str) -> int:
    key = _key(slug)
    cache.add(key, 0)
    return cache.incr(key)
```

The in-process counters are later persisted in one transaction:

```bash
uv run python manage.py flush_clicks
# flushed 42 clicks
```

[`flush_clicks`](shortener/management/commands/flush_clicks.py) is a standard Django management command that calls [`shortener.cache.flush_clicks()`](shortener/cache.py). The flush subtracts its snapshot with `cache.decr` instead of deleting the key, so a click that lands between the snapshot and the write survives for the next flush.

### 13. Counting dispatched actions with `action_dispatched`

[`receivers.py`](shortener/receivers.py) hangs one receiver off the framework's `action_dispatched` signal, bumps a per-action counter in the cache, and keeps an index of the names seen so far:

```python
@receiver(action_dispatched)
def _on_action_dispatched(action_name: str, **kwargs) -> None:
    key = _key(action_name)
    cache.add(key, 0)
    cache.incr(key)
    _remember(action_name)
```

`AppConfig.ready()` imports the module so the receiver connects at startup, and [`admin/stats/page.py`](shortener/routes/admin/stats/page.py) exposes `action_counts()` as the `form_actions` context. Submitting the create form, an inline edit, a delete, or a clicks reset moves a row in that card without any of those handlers knowing the counter exists.

## Gotchas

### PEP 563 and DI annotations

The DI resolver inspects `inspect.signature(func).parameters[...].annotation`. Under `from __future__ import annotations` that annotation is a **string**, and `typing.get_origin(string)` returns `None` — your `DLink[Link]` parameter will not be resolved.

Two rules:

1. **Do not use `from __future__ import annotations` in a `page.py` or `component.py` that declares DI-injected parameters** (see [`admin/links/[slug]/page.py`](shortener/routes/admin/links/[slug]/page.py)).
2. **Types used in DI annotations must be runtime-importable**, not hidden behind `if TYPE_CHECKING`. The resolver uses `typing.get_type_hints` to evaluate strings and needs the type in module globals.

### An unresolvable `{% component %}` prop renders empty

`{% component "card" title=some_var %}` resolves `some_var` against the template context. A name that is not there resolves to Django's `string_if_invalid` instead of raising, so a typo in a prop name shows up as a blank slot rather than an error. Quoted literals are demoted from `SafeString` to plain `str` so `{{ prop }}` autoescapes — opt back in with `prop=value|safe`.

## Further reading

- [next/urls/backends.py](../../next/urls/backends.py) — file router implementation.
- [next/deps/providers.py](../../next/deps/providers.py) — DI base classes used by `DLink`.
- [next/forms/dispatch/](../../next/forms/dispatch/) — form action dispatch pipeline.
- [next/components/context.py](../../next/components/context.py) — `@component.context` mechanics.
- [next/pages/loaders.py](../../next/pages/loaders.py) — layout composition logic.
- [next/partial/](../../next/partial/) — zones, patch envelopes, and the fallback contract used in section 6.
