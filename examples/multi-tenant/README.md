# Multi-tenant notes

A workspace for two independent tenants (Acme and Globex) that share the same Django project, the same page tree, and the same static pipeline. Each request is scoped to one tenant, the page tree resolves notes through that tenant, the static pipeline rewrites every asset URL with a per-tenant prefix, and the chrome reads its accent color from a request-derived CSS variable.

## What you will see

| URL | Description |
| --- | --- |
| `/` | Workspace landing for the active tenant. Welcome card plus the five most recent notes. |
| `/notes/` | All notes that belong to the active tenant, rendered as `note_card` composites. |
| `/notes/new/` | Create a note. The handler stamps the active tenant on the row and redirects to its editor. |
| `/notes/<id>/edit/` | Note editor with title and body inputs and a `markdown_preview` pane. |
| `/_t/<slug>/static/<path>` | The per-tenant asset prefix every `<link>` and `<script>` URL carries, the `next.min.js` runtime included, forwarded to Django staticfiles by [`config/urls.py`](config/urls.py). |

Two tenants ship with the example in [`notes/demo.py`](notes/demo.py):

| slug     | name               | accent            |
| -------- | ------------------ | ----------------- |
| `acme`   | Acme Industries    | `#2563eb` (blue)  |
| `globex` | Globex Corporation | `#16a34a` (green) |

The header pill carries the tenant name, the accent strip and accent text use the CSS variable surfaced by the `tenant_theme` context processor, and every `<link>` and `<script>` URL, the `next.min.js` runtime bundle included, is prefixed with `/_t/acme/` or `/_t/globex/`.

## How to run

```bash
cd examples/multi-tenant
uv run python manage.py migrate
uv run python manage.py seed_demo
uv run python manage.py runserver     # http://127.0.0.1:8000/
uv run pytest
```

`seed_demo` writes two tenants and three demo notes from [`notes/demo.py`](notes/demo.py). The Acme `Status update` note is seeded locked, so the editor's object-level guard has something to refuse. Migrations carry schema only, so a database without that command has no tenant and every request answers `400`.

There are two ways to drive the app:

- **Browser (DEBUG only).** Open `http://127.0.0.1:8000/notes/?tenant=acme`. The middleware sets a `next_tenant` cookie and redirects to a clean URL. Subsequent navigation reuses the cookie. Switch tenants with `?tenant=globex`. The query parameter and cookie path are guarded by `settings.DEBUG=True` and exist only to make the demo viewable without a header-injecting browser extension. A cookie naming a tenant that no longer exists answers `404` and deletes the cookie, so a renamed slug does not wedge the browser on an unreachable workspace.
- **API / production.** Send the `X-Tenant` header explicitly:

  ```bash
  curl -H 'X-Tenant: acme' http://127.0.0.1:8000/notes/
  curl -H 'X-Tenant: globex' http://127.0.0.1:8000/notes/
  ```

  The query and cookie fallbacks are disabled outside `DEBUG`. A request without the header returns `400 Missing X-Tenant header.`.

Tailwind loads via the Play CDN in the shared [`page_head`](../_shared/_components/page_head/component.djx) component. No Node, no build step. [`root_pages/layout.djx`](root_pages/layout.djx) calls it in block form and fills its `extra` slot with the `.accent-bar` / `.accent-text` / `.accent-border` rules, which read `var(--tenant-accent)` with the shared primary colour as the fallback. The variable itself is set once as an inline `style` on `<body>` from `tenant_theme_css`, so a page rendered without a tenant still has a usable palette.

## Walking the code

### 1. The tenant resolution chain

The chain has three links:

1. [`notes/middleware.py`](notes/middleware.py) parses `X-Tenant` and looks up the matching `Tenant` row. Missing slug → `400`. Unknown slug → `404`. Match → `request.tenant = tenant`. Paths under `/_t/` return early without resolving anything, because the per-tenant asset URLs of section 2 already carry the slug in the path and a browser never puts the header on an asset request.
2. [`notes/providers.py`](notes/providers.py) defines `DTenant` (a `DDependencyBase` marker) and `TenantProvider`, a `RegisteredParameterProvider`. The provider matches when `param.annotation is DTenant` and `request.tenant` is set. `apps.py` imports the module on startup so the auto-registry picks it up.
3. Pages and form actions request the tenant by name and type:

   ```python
   @context("notes")
   def notes(active_tenant: DTenant) -> list[Note]:
       return list(Note.objects.filter(tenant=active_tenant))
   ```

   The framework injects the `Tenant` instance directly. Page modules in the example do not start with `from __future__ import annotations`, because the DI resolver compares parameter annotations by identity.

### 2. Per-tenant static URL prefix

The custom backend lives in [`notes/backends.py`](notes/backends.py). It overrides only `asset_url`, the request-aware URL hook of `StaticBackend`. The `request` keyword argument is the hook that core threads through `StaticManager.inject(...)`. For absolute URLs (CDN strings) the method falls back to the unmodified URL.

```python
class TenantPrefixStaticBackend(StaticFilesBackend):
    def asset_url(self, url, *, request=None):
        tenant = get_active_tenant(request) if request is not None else None
        if tenant is None or not url.startswith("/"):
            return url
        return PREFIX_FORMAT.format(slug=tenant.slug) + url
```

One override is enough because every URL the pipeline renders goes through `asset_url` — the co-located `<link>` and `<script>` tags, the `next.min.js` runtime tag, and its `<link rel="preload">` hint. Rewriting inside `render_*_tag` instead would leave the runtime bundle on the unprefixed URL, because core builds that tag from `NEXT_JS_OPTIONS` rather than from a renderer method.

The settings entry is a single line:

```python
"STATIC_BACKENDS": [
    {"BACKEND": "notes.backends.TenantPrefixStaticBackend"},
]
```

The `next.static` collector caches deduplicated URLs once. The `asset_url` hook lets you decorate URLs at injection time without forking that cache.

The prefix has to resolve to a file for the demo to render, so [`config/urls.py`](config/urls.py) maps `^_t/(?P<slug>[^/]+)/static/(?P<path>.*)$` to a view that drops the slug and forwards to `django.contrib.staticfiles.views.serve`. A real deployment points a CDN at `STATIC_URL` and lets the prefix decorate cache keys instead of routing.

### 3. Shared root layout via `DIRS`

The `notes` Django app does not own its HTML shell. The root template lives under [`root_pages/layout.djx`](root_pages/layout.djx) and the global components live under [`root_blocks/header/`](root_blocks/header/) and [`root_blocks/footer/`](root_blocks/footer/). The page and component backends pick them up through the `DIRS` setting:

```python
"PAGE_BACKENDS": [
    {
        "BACKEND": "next.urls.FileRouterBackend",
        "APP_DIRS": True,
        "DIRS": [BASE_DIR / "root_pages"],
        "PAGES_DIR": "workspaces",
        ...
    },
],
"COMPONENT_BACKENDS": [
    {
        "BACKEND": "next.components.FileComponentsBackend",
        "DIRS": [BASE_DIR / "root_blocks", SHARED_DIR / "_components"],
        "COMPONENTS_DIR": "_blocks",
    },
],
```

This is the canonical way to share chrome across multiple Django apps. See [`docs/content/topics/project-layout.rst`](../../docs/content/topics/project-layout.rst) for the broader pattern.

The `<main>` element in that layout delegates its width to the shared [`container`](../_shared/_components/container.djx) component. A wrapper with a single insertion point needs no named slot, so the layout calls it in block form. Everything between the opening and closing tag arrives as free children, which the component splices with `{{ children }}`. The `{% block template %}` of the page renders inside that call, so every page in the example lands in the same centred column.

### 4. Inherit context for the active tenant

[`notes/workspaces/page.py`](notes/workspaces/page.py) registers two `@context` callables, and only the first one is inherited:

```python
@context("tenant", inherit_context=True)
def tenant(active_tenant: DTenant) -> "Tenant":
    return active_tenant


@context("recent_notes")
def recent_notes(active_tenant: DTenant) -> list[Note]:
    return list(Note.objects.filter(tenant=active_tenant)[:5])
```

`inherit_context=True` lifts `tenant` to every descendant page, so [`notes/layout.djx`](notes/workspaces/notes/layout.djx) and both form templates print `tenant.slug` without re-resolving anything. `recent_notes` carries no flag, so it stays on the landing page that renders it and the `/notes/` list does not pay for a query it never shows.

### 5. The two note forms and the shared preview

[`notes/workspaces/notes/new/page.py`](notes/workspaces/notes/new/page.py) defines `NoteCreateForm`, a plain `next.forms.Form` with no model behind it. The template reaches it as `{% form "note_create_form" %}`, the snake_case name the framework derives from the class. Its `on_valid` takes `active_tenant: DTenant` and passes it straight to `Note.objects.create`, so the tenant is stamped on the row by the same provider the pages use and the browser never sees a tenant field it could tamper with. The redirect goes to the new note's editor, not back to the list, so the author keeps typing where they left off.

Both forms declare their widgets with `next.forms.ComponentWidget`, naming the shared kit's `input` and `textarea` components and passing `rows` and `placeholder` through as component props, so `{{ form.title }}` renders through the shared kit's `input` and `textarea` components instead of Django's default HTML. A `ComponentWidget` naming a component that does not resolve is reported as `next.W054` at `manage.py check` time.

[`notes/workspaces/notes/[int:note_id]/edit/page.py`](notes/workspaces/notes/[int:note_id]/edit/page.py) defines `NoteEditForm`, a `ModelForm` whose `Meta.fields` lists only `title` and `body`. There is no extra id field. The form binds to an instance through `get_initial`, which reads the URL kwarg `note_id`, derives the tenant from the request via `get_active_tenant(request)`, and routes the lookup through `get_object_or_404(Note, pk=note_id, tenant=tenant)`, so a tenant requesting another tenant's note id receives a `404`. On a valid submission `on_valid` calls `self.save()` and redirects back to the editor.

The body textarea is rendered side by side with the shared `markdown_preview` shell ([`examples/_shared/_components/markdown_preview/`](../_shared/_components/markdown_preview/)). The shell is pure presentation. This example renders the body server-side in [`notes/markdown_render.py`](notes/markdown_render.py), which imports the `markdown` package, escapes the raw body before rendering, strips unsafe link URLs, and wraps the result in `SafeString`. Each page injects the HTML through the `rendered_html` prop, so the shell shows what the app rendered. The create page has no body yet and renders the empty string, which `render_markdown` answers with its placeholder paragraph, so the pane is never a blank box on first paint. The shell's co-located `component.mjs` is auto-discovered and served as a module script. `TenantPrefixStaticBackend` rewrites its `/static/next/components/markdown_preview.mjs` URL to `/_t/<slug>/static/...` so the script rides the same per-tenant prefix as the co-located CSS. Only the server-side render stays local — the shell and the client behaviour are shared with the wiki form.

### 6. Dynamic permission hooks on the edit form

The `get_object_or_404` in `get_initial` keeps one tenant from loading another tenant's note id. It does not cover two rules that are not about ownership. `NoteEditForm` layers those as the framework's two DI-resolved permission hooks.

```python
class NoteEditForm(ModelForm):
    @classmethod
    def check_permissions(cls, tenant: DTenant) -> PermissionOutcome:
        return tenant.is_active

    def has_object_permission(self) -> PermissionOutcome:
        return not self.instance.locked
```

`check_permissions` is the view-level gate. The dispatcher resolves its parameters the same way it resolves `get_initial`, so the hook receives the active `Tenant` through the `DTenant` provider and declares nothing else. It runs after the static `ActionGuard` and before `get_initial`, so a suspended tenant (`is_active=False`) is refused before any note is loaded. The middleware resolves a suspended tenant exactly like an active one, and the ownership `404` would still let a suspended tenant reach its own notes, so this rule exists only at the hook layer. Returning `False` denies with `403`.

`has_object_permission` is the object-level gate. It runs after binding, so `self.instance` is the loaded note. A `locked` note belongs to its tenant and loads without a `404`, yet must stay read-only, so the hook returns `False` to deny with a bare `403` and no re-render. Returning `True` allows the edit through to `is_valid`.

A bare `403` carries no body to re-render, so the editor says up front what the guard will refuse. The template reads the same `locked` flag off the `note` context, shows a warning alert, and disables the save button. The disabled button only announces the rule. The hook is what enforces it.

Both hooks emit `next.signals.form_access_denied` on a denial, carrying the `action_name`, `uid`, `request`, the `layer` (`"view"` or `"object"`), and the `reason` (`"raised"`, `"denied"`, or `"response"`). The signal stays silent when both hooks allow the request.

The `notes` app consumes that signal. [`notes/receivers.py`](notes/receivers.py) connects a receiver that logs each denial under the `notes.access` logger, reading the active tenant through `get_active_tenant` so the line names the tenant whose edit was refused. `NotesConfig.ready` imports the module so the connection is live at startup.

```python
from .access import get_active_tenant


@receiver(form_access_denied)
def _on_form_access_denied(action_name, layer, reason, request, **kwargs):
    tenant = get_active_tenant(request)
    tenant_slug = getattr(tenant, "slug", "unknown")
    logger.warning(
        "form access denied action=%s layer=%s reason=%s tenant=%s",
        action_name,
        layer,
        reason,
        tenant_slug,
    )
```

## Gotchas

### The asset-version guard needs an explicit version

The example pins an explicit asset `VERSION` in `PARTIAL_BACKENDS`, the shared convention explained in the [examples README](../README.md#conventions-every-example-follows).

## Further reading

- [`next/static/backends.py`](../../next/static/backends.py) — the `StaticBackend` ABC with the `asset_url` hook and its `request=` kwarg.
- [`next/static/manager.py`](../../next/static/manager.py) — the `StaticManager.inject` call site that threads `request`.
- [`next/urls/backends.py`](../../next/urls/backends.py) — the `FileRouterBackend.DIRS` handling that makes `root_pages/` work.
- [`next/components/backends.py`](../../next/components/backends.py) — the matching `FileComponentsBackend.DIRS` handling for `root_blocks/`.
- [`next/deps/providers.py`](../../next/deps/providers.py) — the `RegisteredParameterProvider` ABC used by `TenantProvider`.
- [`next/pages/registry.py`](../../next/pages/registry.py) — the `inherit_context` walk that lifts `tenant` to every descendant page.
- [`docs/content/topics/static-assets/backends.rst`](../../docs/content/topics/static-assets/backends.rst) — the request-aware output section that this example anchors.
- [`docs/content/topics/dependency-injection.rst`](../../docs/content/topics/dependency-injection.rst) — the request-scoped provider pattern.
- [`docs/content/howto/enforce-object-level-permissions.rst`](../../docs/content/howto/enforce-object-level-permissions.rst) — the `check_permissions` and `has_object_permission` hooks used in section 6.
- [`docs/content/topics/forms/signals.rst`](../../docs/content/topics/forms/signals.rst) — the `form_access_denied` payload contract.
- [`docs/content/ref/system-checks.rst`](../../docs/content/ref/system-checks.rst) — `next.W054` for `ComponentWidget` and `next.W069` for the asset-version guard.
