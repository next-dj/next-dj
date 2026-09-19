# Multi-tenant notes

A workspace for two independent tenants (Acme and Globex) that share the same Django project, the same page tree, and the same static pipeline. Each request is scoped to one tenant, the page tree resolves notes through that tenant, the static pipeline rewrites every asset URL with a per-tenant prefix, and the chrome reads its accent color from a request-derived CSS variable.

> **Trust boundary.** The `X-Tenant` header is attacker-controlled. Anybody can send `X-Tenant: globex` with `curl`, so this shape isolates tenants only behind a reverse proxy that owns the header. That proxy must be the single route to the application, the application must bind to an address the public network cannot reach, the proxy must derive the slug from something it owns — a host mapping, a client certificate, its own session — rather than from anything the client sent, and it must set the header on every request it forwards so an inbound copy is overwritten instead of passed along. A proxy that fills the header in only when it is absent keeps the client's forged value, and that single misconfiguration removes the isolation entirely. Run this example with nothing in front of it and every visitor picks their own tenant.
>
> Two shapes need no proxy at all: read the tenant out of the signed-in user's membership rows, or out of `request.get_host()`, which `ALLOWED_HOSTS` narrows to the names the project publishes. [`docs/content/howto/scope-requests-per-tenant.rst`](../../docs/content/howto/scope-requests-per-tenant.rst) puts all three side by side in trust order. This example reads the header because that is the shape which shows a custom `RegisteredParameterProvider` and a request-aware static backend with the fewest moving parts, not because it is the shape to reach for first.

## What you will see

| URL | Description |
| --- | --- |
| `/` | Workspace landing for the active tenant. Welcome card plus the five most recent notes. |
| `/notes/` | All notes that belong to the active tenant, rendered as `note_card` composites. |
| `/notes/new/` | Create a note. The handler stamps the active tenant on the row and redirects to its editor. |
| `/notes/<id>/edit/` | Note editor with title and body inputs and a `markdown_preview` pane. |
| `/_t/<slug>/static/<path>` | The per-tenant asset prefix every `<link>` and `<script>` URL carries, the `next.min.js` runtime included, forwarded to Django staticfiles by [`config/urls.py`](config/urls.py). |

Two tenants ship with the example in [`notes/demo.py`](notes/demo.py):

| slug | name | accent | stylesheet |
| --- | --- | --- | --- |
| `acme` | Acme Industries | `#2563eb` (blue) | `notes/css/acme.css`, a staticfiles name |
| `globex` | Globex Corporation | `#16a34a` (green) | `/static/notes/css/theme.css`, a ready URL |

The header pill carries the tenant name, the accent strip and accent text use the CSS variable surfaced by the `tenant_theme` context processor, and every `<link>` and `<script>` URL, the `next.min.js` runtime bundle included, is prefixed with `/_t/acme/` or `/_t/globex/`.

## How to run

```bash
cd examples/multi-tenant
uv run python manage.py migrate
uv run python manage.py seed_demo
uv run python manage.py runserver     # http://127.0.0.1:8000/
uv run pytest
```

`seed_demo` writes two tenants and three demo notes from [`notes/demo.py`](notes/demo.py). The Acme `Status update` note is seeded locked, so the editor's object-level guard has something to refuse. Migrations carry schema only, so a database without that command has no tenant row at all: a request that carries no tenant answers `400` and one that names a slug answers `404`.

There are two ways to drive the app:

- **Browser (DEBUG only).** Open `http://127.0.0.1:8000/notes/?tenant=acme`. The middleware sets a `next_tenant` cookie and redirects to a clean URL. Subsequent navigation reuses the cookie. Switch tenants with `?tenant=globex`. The query parameter and cookie path are guarded by `settings.DEBUG=True` and exist only to make the demo viewable without a header-injecting browser extension. A cookie naming a tenant that no longer exists answers `404` and deletes the cookie, so a renamed slug does not wedge the browser on an unreachable workspace.
- **Header.** Send `X-Tenant` explicitly, which is what the reverse proxy of a deployment does on every request it forwards:

  ```bash
  curl -H 'X-Tenant: acme' http://127.0.0.1:8000/notes/
  curl -H 'X-Tenant: globex' http://127.0.0.1:8000/notes/
  ```

  Choosing the tenant by hand like this is precisely the forgery the trust boundary above describes, and against a bare development server it works — which is why the boundary is stated rather than implied. The query and cookie fallbacks are disabled outside `DEBUG`. A request with no tenant at all returns `400 Missing X-Tenant header.`, and in `DEBUG` the body appends a one-line pointer at the query affordance. A slug that matches no row returns `404 Unknown tenant.`. Neither body repeats what the client sent: a body quoting the submitted slug is an oracle for enumerating tenant names, and in an HTML response it is a reflected-XSS sink.

Tailwind loads via the Play CDN in the shared [`page_head`](../_shared/_components/page_head/component.djx) component. No Node, no build step. [`root_pages/layout.djx`](root_pages/layout.djx) calls it in block form and fills its `extra` slot with two `{% use_style %}` registrations. The first names [`static/notes/css/theme.css`](static/notes/css/theme.css), which holds the `.accent-bar` / `.accent-text` / `.accent-border` rules and reads `var(--tenant-accent)` with the shared primary colour as the fallback. The second names whatever the active tenant carries in `Tenant.stylesheet`, and section 2 walks what happens to each of those values. The variable itself is set once as an inline `style` on `<body>` from `tenant_theme_css`, so a page rendered without a tenant still has a usable palette.

## Walking the code

### 1. The tenant resolution chain

The chain has three links:

1. [`notes/middleware.py`](notes/middleware.py) parses `X-Tenant` and looks up the matching `Tenant` row. Missing slug → `400`. Unknown slug → `404`. Match → `request.tenant = tenant`. Both refusals answer with a fixed body built from a module constant, never from the submitted slug, and the middleware docstring carries the proxy obligations the header shape rests on. Paths under `/_t/` return early without resolving anything, because the per-tenant asset URLs of section 2 already carry the slug in the path and a browser never puts the header on an asset request. That early return is safe only because nothing tenant-owned lives under the prefix: [`config/urls.py`](config/urls.py) discards the slug and serves the same shared file whichever tenant a URL names, so the prefix is a cache key rather than an isolation boundary, and no page route can be reached through it.
2. [`notes/providers.py`](notes/providers.py) defines `DTenant` (a `DDependencyBase` marker) and `TenantProvider`, a `RegisteredParameterProvider`. The provider matches when `param.annotation is DTenant` and `request.tenant` is set. `static_can_handle` answers `False` for any other annotation, so the plan compiler keeps the provider out of unrelated parameters, and `None` for `DTenant`, whose tenant check stays in `can_handle`. `apps.py` imports the module on startup so the auto-registry picks it up.
3. Pages and form actions request the tenant by name and type:

   ```python
   @context("notes")
   def notes(active_tenant: DTenant) -> list[Note]:
       return list(Note.objects.filter(tenant=active_tenant))
   ```

   The framework injects the `Tenant` instance directly. Every query in the example that reaches a tenant-owned row carries that filter: the two note listings, the `Note.objects.create` of the create form, and the `get_object_or_404(Note, pk=note_id, tenant=tenant)` behind both the editor's `note` context and its `get_initial`. Middleware that attaches a tenant repairs nothing if one queryset forgets to use it. Page modules never start with `from __future__ import annotations` and import `DTenant` at runtime. The resolver does evaluate string hints through `get_type_hints`, but a single name it cannot evaluate — a marker or a model imported only under `if TYPE_CHECKING` — drops the whole callable back to its raw annotations, where `get_origin` sees a string and the parameter silently falls through to another provider.

### 2. Per-tenant asset URLs

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

The settings entries are two lines:

```python
"STATIC_BACKENDS": [
    {"BACKEND": "notes.backends.TenantPrefixStaticBackend"},
],
"STATIC_VERSION": ASSET_BUILD_ID,
```

The `next.static` collector caches deduplicated URLs once. The `asset_url` hook lets you decorate URLs at injection time without forking that cache.

Overriding `asset_url` alone leaves `resolve_url` inherited, and the two hooks run at different moments. `resolve_url` turns an authored reference into a public URL while the asset is registered, and `asset_url` decorates that URL for the request being rendered. The root layout registers the shared sheet by name and the active tenant's own sheet straight from the column that holds it:

```django
{% use_style "notes/css/theme.css" %}
{% if tenant_stylesheet %}{% use_style tenant_stylesheet %}{% endif %}
```

Acme reads `/_t/acme/static/notes/css/acme.css?v=2026.09.1` out of that second line. Three layers wrote the string and none of them knows about the others. Django staticfiles turned the name `notes/css/acme.css` into `/static/notes/css/acme.css`, the tenant backend prepended `/_t/acme`, and `STATIC_VERSION` stamped the build id. A backend that rewrites URLs composes on top of name resolution for free, which is the whole point of overriding one hook rather than the tag renderers.

`Tenant.stylesheet` holds whatever the tenant was provisioned with, and the two demo rows are provisioned differently on purpose. `notes/css/acme.css` has no scheme, no host, no query and no leading slash, so it is a name and storage resolves it. `/static/notes/css/theme.css` starts with a slash, so it is already a URL and the pipeline hands it back untouched. The shape of the value decides, not the tag it arrived in and not where it came from.

The Globex row is the interesting one. The layout already registered `notes/css/theme.css` by name for every tenant, and Globex names that same file by path. Both spellings reach the collector as `/static/notes/css/theme.css`, the dedup key is the resolved URL, and the head carries one `<link>` rather than two. A project rewriting hardcoded paths into names one template at a time never double-loads a file halfway through the move.

`STATIC_VERSION` reads `ASSET_BUILD_ID` from [`config/settings.py`](config/settings.py), which takes `NOTES_BUILD_ID` out of the environment and falls back to a literal for a checkout nobody deployed. A build id has to arrive from the deploy rather than be computed at import time, otherwise every process in a fleet stamps a different one and a shared cache never settles. The stamp lands on every URL the pipeline emits, so the vendor script of the shared markdown preview renders as `https://cdn.jsdelivr.net/npm/marked/marked.min.js?v=2026.09.1` too. This example serves its assets straight off disk, so the query parameter is the only thing that changes when a file changes. A project on `ManifestStaticFilesStorage` gets that from the filename instead and wants neither setting, which is what [`observability`](../observability/) shows.

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

The `<main>` element in that layout delegates its width to the shared [`container`](../_shared/_components/container.djx) component. A wrapper with a single insertion point needs no named slot, so the layout calls it in block form. Everything between the opening and closing tag arrives as free children, which the component splices with `{{ children }}`. The `{% template %}` of the page renders inside that call, so every page in the example lands in the same centred column.

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

The body textarea is rendered side by side with the shared `markdown_preview` shell ([`examples/_shared/_components/markdown_preview/`](../_shared/_components/markdown_preview/)). The shell is pure presentation. The body is rendered server-side by `render_markdown` from the shared [`examples/_shared/markup.py`](../_shared/markup.py), which escapes the raw body before the `markdown` package sees it, strips unsafe link URLs, and wraps the result in `SafeString`. Each page injects the HTML through the `rendered_html` prop, so the shell shows what the app rendered. The create page has no body yet and renders the empty string, which `render_markdown` answers with its placeholder paragraph, so the pane is never a blank box on first paint. The shell's co-located `component.mjs` is auto-discovered and served as a module script. `TenantPrefixStaticBackend` rewrites its `/static/next/components/markdown_preview.mjs` URL to `/_t/<slug>/static/...` so the script rides the same per-tenant prefix as the co-located CSS. The shell, the client behaviour, and the server-side render are all shared with the wiki form.

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

The example pins an explicit asset `VERSION` in `PARTIAL_BACKENDS`, the shared convention explained in the [examples README](../README.md#conventions-every-example-follows). Here the pin is the same `ASSET_BUILD_ID` that feeds `STATIC_VERSION`, so one deploy value stamps both the asset URLs and the guard that tells an open tab its JavaScript is stale.

## Further reading

- [`next/static/backends.py`](../../next/static/backends.py) — the `StaticBackend` ABC with the `asset_url` hook and its `request=` kwarg, plus the `resolve_url` hook this example inherits.
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
