# URL shortener

A bit.ly-style mini-product built on **next-dj**. Paste a long URL, get a short slug, share it. Every visit to `/s/<slug>/` is counted in `LocMemCache`, and a management command flushes the counters into SQLite.

The example is small on purpose. It is a tour of the framework surface you will use 90% of the time: the file router, layouts, context functions, forms, components, a custom DI provider, URL reversing, active-link highlighting, and a cache-backed hot path.

## What you will see

| URL | Description |
|-----|-------------|
| `/` | Form to shorten a URL, list of the latest entries. |
| `/s/<slug>/` | 302 redirect to the original URL, bumps the click counter. |
| `/admin/` | Top links and unflushed click counters. Nested admin layout with a subnav. |
| `/admin/stats/` | Totals: links, persisted clicks, pending clicks. |
| `/admin/links/<slug>/` | Link detail, resolved through a custom `DLink[Link]` DI provider. |

## How to run

```bash
cd examples/shortener
uv run python manage.py migrate
uv run python manage.py runserver     # http://127.0.0.1:8000/
uv run pytest
```

Tailwind loads via the Play CDN in [`routes/layout.djx`](shortener/routes/layout.djx). No Node, no build step.

## Walking the code

### 1. Rename `pages/` and `_components/` to fit your domain

[`config/settings.py`](config/settings.py) sets both directory names under `NEXT_FRAMEWORK`:

```python
NEXT_FRAMEWORK = {
    "DEFAULT_PAGE_BACKENDS": [{
        "BACKEND": "next.urls.FileRouterBackend",
        "APP_DIRS": True,
        "PAGES_DIR": "routes",
    }],
    "DEFAULT_COMPONENT_BACKENDS": [{
        "BACKEND": "next.components.FileComponentsBackend",
        "COMPONENTS_DIR": "_widgets",
    }],
}
```

The framework does not hardcode the names. `routes/` could be `screens/`, `views/`, `panels/` — pick what matches your product.

### 2. Page, template, layout — how a URL is rendered

A directory under `routes/` with a `page.py` becomes a URL. The framework composes a template in three layers:

- **`layout.djx`** (any ancestor directory) — the outer shell. Must contain an empty placeholder `{% block template %}{% endblock template %}` where the child content is substituted.
- **`template.djx`** (sibling of `page.py`) — the page body. Just HTML. No `{% block template %}` wrapping needed because the framework handles substitution.
- **`page.py`** — Python side: context functions (`@context`), optional form actions (`@forms.action`), optional `template = "..."` module attribute, optional `render(request, ...) -> HttpResponse`.

Ancestor layouts cascade: `routes/admin/stats/` inherits `routes/admin/layout.djx`, which itself is wrapped by `routes/layout.djx`. Look at the nested toolbar in [`admin/layout.djx`](shortener/routes/admin/layout.djx):

```djx
<section class="space-y-4">
  <div class="flex items-center justify-between border-b pb-3">
    <h1 class="text-2xl font-bold">Admin panel</h1>
    <nav>…subnav…</nav>
  </div>

  {% block template %}{% endblock template %}
</section>
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
    return {
        "link": link,
        "cache_key": f"{CLICK_PREFIX}{link.slug}",
    }
```

`@context("link")` + `@context("cache_key")` would each trigger the `DLink` provider and hit the database twice. The unkeyed form runs the dependency once, merges the dict into the template context.

**Direct registration** of an already-existing function — no wrapper needed:

```python
# routes/admin/page.py
from shortener.cache import pending_clicks

context("pending_clicks")(pending_clicks)
```

`context("key")` returns the decorator, and calling it on a function registers the function exactly like `@context("key")` would.

### 4. `inherit_context=True` — sharing context down the tree

```python
@context("recent_links", inherit_context=True)
def recent_links() -> list[Link]:
    return list(Link.objects.order_by("-clicks", "-created_at")[:10])
```

Declared once in [`admin/page.py`](shortener/routes/admin/page.py), available in `admin/stats/` and `admin/links/<slug>/` templates. Use it for toolbar-level data. Do not mark heavy queries `inherit_context=True` unless every sub-page actually needs them.

### 5. Forms — `@forms.action` + `{% form %}`

[`routes/page.py`](shortener/routes/page.py) declares the handler:

```python
@action("create_link", form_class=CreateLinkForm)
def create_link(form: CreateLinkForm) -> HttpResponseRedirect:
    _create_link_with_unique_slug(form.cleaned_data["url"])
    return HttpResponseRedirect("/")
```

The handler receives only the parameters it declares. No unused `request` — the DI resolver only fills what the signature asks for.

[`routes/template.djx`](shortener/routes/template.djx) renders the form:

```djx
{% form @action="create_link" class="space-y-3 …" %}
  {{ form.url }}
  {% if form.errors %}<p class="text-rose-600">{{ form.url.errors|first }}</p>{% endif %}
  <button type="submit">Shorten</button>
{% endform %}
```

The `{% form @action="…" %}` tag resolves the action to its stable UID endpoint and injects a CSRF token.

> `{% form %}`, `{% component %}`, `{% collect_styles %}`, `{% url %}` etc. are all globally loaded template tags. **Do not** write `{% load forms components next_static %}` — `next.apps.templates.install()` registers them as Django builtins at startup.

### 6. Components — simple, composite, and shared

A component lives in `_widgets/<name>/`:

- **Simple**: just `component.djx` — pure template.
- **Composite**: `component.py` + `component.djx`. The Python side adds context via `@component.context("key")`.

[`_widgets/link_card/`](shortener/routes/_widgets/link_card/) is a composite. The template renders a card while the Python function computes a display URL via `reverse`:

```python
@component.context("short_url")
def _short_url(link: Link) -> str:
    return reverse("slug_redirect", kwargs={"slug": link.slug})
```

Usage in a loop:

```djx
{% for link in recent_links %}
  {% component "link_card" %}
{% endfor %}
```

`{% component "name" %}` accepts only **literal string props**. To pass the loop variable, the framework automatically forwards the parent template's flattened context, so the `link` loop variable lands inside the component and `ContextByNameProvider` fills the `link: Link` parameter of `_short_url`.

### 7. Shared `nav_link` — DRY the active-state logic

Root nav and admin subnav both need the same active-state rule. The logic lives once in [`_widgets/nav_link/component.py`](shortener/routes/_widgets/nav_link/component.py):

```python
@component.context("href")
def _href(url_name: str) -> str:
    return reverse(url_name)


@component.context("is_active")
def _is_active(url_name: str, request: HttpRequest, active_when: str = "") -> bool:
    view_name = request.resolver_match.view_name
    if active_when:
        return active_when in view_name
    return view_name == url_name
```

Usage:

```djx
{# exact match #}
{% component "nav_link" url_name="next:page_admin_stats" label="Stats" %}

{# prefix match — stays active across every URL name that contains 'page_admin' #}
{% component "nav_link" url_name="next:page_admin" active_when="page_admin" label="admin" class_base="text-sm" %}
```

No `request.path` string-munging, no custom template tag, no context processor. Django populates `request.resolver_match.view_name` and the component reads it.

### 8. URL names and `{% url %}`

Every anchor in the project goes through `{% url %}`. File-router URLs sit under the `next` namespace. The name format is `page_<prepare_url_name(url_path)>`:

| File | URL | Name |
|------|-----|------|
| `routes/page.py` | `/` | `next:page_` |
| `routes/admin/page.py` | `/admin/` | `next:page_admin` |
| `routes/admin/stats/page.py` | `/admin/stats/` | `next:page_admin_stats` |
| `routes/admin/links/[slug]/page.py` | `/admin/links/<slug>/` | `next:page_admin_links_slug` |

Use them as `{% url 'next:page_admin' %}` or with args: `{% url 'next:page_admin_links_slug' slug=link.slug %}`. Rename files freely — templates stay correct because they never hardcode paths.

### 9. Custom DI provider — `DLink[Link]`

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
def _link(link: DLink[Link]) -> Link:
    return link
```

### 10. Interop with a plain Django view — `/s/<slug>/`

The file router composes a template for every page, and the router prefers the template path over a module-level `render()` function. A redirect-only endpoint therefore cannot live under `routes/`: the root layout would wrap it in HTML and the `render()` function would never be invoked.

The clean split:

- Navigable pages → [`routes/`](shortener/routes/) under the layout.
- The pure redirect → a plain Django view in [`views.py`](shortener/views.py), mounted in [`config/urls.py`](config/urls.py) **before** `include('next.urls')`:

```python
urlpatterns = [
    path("s/<slug:slug>/", redirect_slug, name="slug_redirect"),
    path("", include("next.urls")),
]
```

The file router never forces you to route everything through it.

### 11. Hot-path cache + flush command

The redirect bumps a counter in `LocMemCache` rather than SQLite:

```python
# cache.py
def increment_clicks(slug: str) -> int:
    key = _key(slug)
    cache.add(key, 0)
    return cache.incr(key)
```

The in-process counter is later persisted in one transaction:

```bash
uv run python manage.py flush_clicks
# flushed 42 clicks
```

[`flush_clicks`](shortener/management/commands/flush_clicks.py) is a standard Django management command that calls `shortener.cache.flush_clicks()`.

## Gotchas

Three framework-specific pitfalls you will hit on your first project.

### PEP 563 and DI annotations

The DI resolver inspects `inspect.signature(func).parameters[...].annotation`. Under `from __future__ import annotations` that annotation is a **string**, and `typing.get_origin(string)` returns `None` — your `DLink[Link]` parameter will not be resolved.

Two rules:

1. **Do not use `from __future__ import annotations` in a `page.py` or `component.py` that declares DI-injected parameters** (see [`admin/links/[slug]/page.py`](shortener/routes/admin/links/[slug]/page.py)).
2. **Types used in DI annotations must be runtime-importable**, not hidden behind `if TYPE_CHECKING`. The resolver uses `typing.get_type_hints` to evaluate strings and needs the type in module globals.

### `{% component %}` props are literal strings

`{% component "card" title="Hello" %}` is valid. `{% component "card" title=some_var %}` is **not** — `some_var` is taken as the literal string `"some_var"`. To pass variables, rely on the parent template context being forwarded (see `link_card` above), or compute values inside the component via `@component.context`.

### Template wins over `render()` for file-routed pages

If any `layout.djx` applies to a `page.py`, the framework renders a template and ignores a top-level `render()` in the module. To write a pure-response view, use a plain Django URL in `config/urls.py` (see `views.py` / `/s/<slug>/`).

## Further reading

- [next/urls/backends.py](../../next/urls/backends.py) — file router implementation.
- [next/deps/providers.py](../../next/deps/providers.py) — DI base classes used by `DLink`.
- [next/forms/dispatch.py](../../next/forms/dispatch.py) — form action dispatch pipeline.
- [next/components/context.py](../../next/components/context.py) — `@component.context` mechanics.
- [next/pages/loaders.py](../../next/pages/loaders.py) — layout composition logic.
