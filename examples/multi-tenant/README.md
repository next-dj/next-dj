# Multi-tenant notes

A workspace for two independent tenants (Acme and Globex) that share the same Django project, the same page tree, and the same static pipeline. Each request is scoped to one tenant, the page tree resolves notes through that tenant, the static pipeline rewrites every asset URL with a per-tenant prefix, and the chrome reads its accent color from a request-derived CSS variable.

## What you will see

| URL | Description |
| --- | --- |
| `/` | Workspace landing for the active tenant. Welcome card plus the five most recent notes. |
| `/notes/` | All notes that belong to the active tenant, rendered as `note_card` composites. |
| `/notes/<id>/edit/` | Note editor with title and body inputs and a live `markdown_preview` pane. |

Two tenants ship with the example via a data migration:

| slug     | name               | accent            |
| -------- | ------------------ | ----------------- |
| `acme`   | Acme Industries    | `#2563eb` (blue)  |
| `globex` | Globex Corporation | `#16a34a` (green) |

The header pill carries the tenant name, the accent strip and accent text use the CSS variable surfaced by the `tenant_theme` context processor, and every `<link>` and `<script>` URL is prefixed with `/_t/acme/` or `/_t/globex/`.

## How to run

```bash
cd examples/multi-tenant
uv run python manage.py migrate
uv run python manage.py runserver     # http://127.0.0.1:8000/
uv run pytest
```

The migration step seeds two tenants and three demo notes through [`notes/migrations/0002_demo_data.py`](notes/migrations/0002_demo_data.py). A later migration ([`notes/migrations/0004_lock_demo_note.py`](notes/migrations/0004_lock_demo_note.py)) locks the Acme `Status update` note so the editor's object-level guard has something to refuse.

There are two ways to drive the app:

- **Browser (DEBUG only).** Open `http://127.0.0.1:8000/notes/?tenant=acme`. The middleware sets a `next_tenant` cookie and redirects to a clean URL. Subsequent navigation reuses the cookie. Switch tenants with `?tenant=globex`. The query parameter and cookie path are guarded by `settings.DEBUG=True` and exist only to make the demo viewable without a header-injecting browser extension.
- **API / production.** Send the `X-Tenant` header explicitly:

  ```bash
  curl -H 'X-Tenant: acme' http://127.0.0.1:8000/notes/
  curl -H 'X-Tenant: globex' http://127.0.0.1:8000/notes/
  ```

  The query and cookie fallbacks are disabled outside `DEBUG`. A request without the header returns `400 Missing X-Tenant header.`.

Tailwind loads via the Play CDN in [`root_pages/layout.djx`](root_pages/layout.djx). No Node, no build step.

## Walking the code

### 1. The tenant resolution chain

The chain has three links:

1. [`notes/middleware.py`](notes/middleware.py) parses `X-Tenant` and looks up the matching `Tenant` row. Missing slug → `400`. Unknown slug → `404`. Match → `request.tenant = tenant`.
2. [`notes/providers.py`](notes/providers.py) defines `DTenant` (a `DDependencyBase` marker) and `TenantProvider`, a `RegisteredParameterProvider`. The provider matches when `param.annotation is DTenant` and `request.tenant` is set. `apps.py` imports the module on startup so the auto-registry picks it up.
3. Pages and form actions request the tenant by name and type:

   ```python
   @context("notes")
   def notes(active_tenant: DTenant) -> list[Note]:
       return list(Note.objects.filter(tenant=active_tenant))
   ```

   The framework injects the `Tenant` instance directly. Page modules in the example do not start with `from __future__ import annotations`, because the DI resolver compares parameter annotations by identity.

### 2. Per-tenant static URL prefix

The custom backend lives in [`notes/backends.py`](notes/backends.py). It overrides only the `render_*_tag` methods of `StaticFilesBackend`. The `request` keyword argument is the hook that core threads through `StaticManager.inject(...)`. For absolute URLs (CDN strings) the helper falls back to the unmodified URL.

```python
class TenantPrefixStaticBackend(StaticFilesBackend):
    def render_link_tag(self, url, *, request=None):
        return super().render_link_tag(_prefixed(url, request))

    def render_script_tag(self, url, *, request=None):
        return super().render_script_tag(_prefixed(url, request))
```

The settings entry is a single line:

```python
"STATIC_BACKENDS": [
    {"BACKEND": "notes.backends.TenantPrefixStaticBackend"},
]
```

The `next.static` collector caches deduplicated URLs once. The `render_*_tag` hook lets you decorate URLs at injection time without forking that cache.

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
        "DIRS": [BASE_DIR / "root_blocks"],
        "COMPONENTS_DIR": "_blocks",
    },
],
```

This is the canonical way to share chrome across multiple Django apps. See [`docs/content/topics/project-layout.rst`](../../docs/content/topics/project-layout.rst) for the broader pattern.

### 4. Inherit context for the active tenant

[`notes/workspaces/page.py`](notes/workspaces/page.py) registers two `@context(..., inherit_context=True)` callables:

```python
@context("tenant", inherit_context=True)
def tenant(active_tenant: DTenant) -> "Tenant":
    return active_tenant


@context("recent_notes")
def recent_notes(active_tenant: DTenant) -> list[Note]:
    return list(Note.objects.filter(tenant=active_tenant)[:5])
```

Every descendant page (`/notes/`, `/notes/<id>/edit/`) sees `tenant` in its template context. The `notes/template.djx` and the `note_card` component read it without re-resolving the tenant, and `NoteCreateForm.on_valid` in [`notes/new/page.py`](notes/workspaces/notes/new/page.py) receives it as a typed `active_tenant: DTenant` parameter through DI.

### 5. The note edit form with a composite preview

[`notes/workspaces/notes/[int:note_id]/edit/page.py`](notes/workspaces/notes/[int:note_id]/edit/page.py) defines `NoteEditForm`, a `ModelForm` whose `Meta.fields` lists only `title` and `body`. There is no extra id field. The form binds to an instance through `get_initial`, which reads the URL kwarg `note_id`, derives the tenant from the request via `get_active_tenant(request)`, and routes the lookup through `get_object_or_404(Note, pk=note_id, tenant=tenant)`, so a tenant requesting another tenant's note id receives a `404`. On a valid submission `on_valid` calls `self.save()` and redirects back to the editor.

The body textarea is rendered side by side with the shared `markdown_preview` shell ([`examples/_shared/_components/markdown_preview/`](../_shared/_components/markdown_preview/)). The shell is pure presentation. This example renders the body server-side in [`notes/markdown_render.py`](notes/markdown_render.py), which imports the `markdown` package, escapes the raw body before rendering, strips unsafe link URLs, and wraps the result in `SafeString`. Each page injects the HTML through the `rendered_html` prop, so the shell shows what the app rendered. The shell's co-located `component.mjs` is auto-discovered and served as a module script. `TenantPrefixStaticBackend` rewrites its `/static/next/components/markdown_preview.mjs` URL to `/_t/<slug>/static/...` so the script rides the same per-tenant prefix as the co-located CSS. Only the server-side render stays local — the shell and the client behaviour are shared with the wiki form.

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

Both hooks emit `next.signals.form_access_denied` on a denial, carrying the `action_name`, `uid`, `request`, the `layer` (`"view"` or `"object"`), and the `reason` (`"raised"`, `"denied"`, or `"response"`). The signal stays silent when both hooks allow the request.

The `notes` app consumes that signal. [`notes/receivers.py`](notes/receivers.py) connects a receiver that logs each denial under the `notes.access` logger, reading the active tenant through `get_active_tenant` so the line names the tenant whose edit was refused. `NotesConfig.ready` imports the module so the connection is live at startup.

```python
from .access import get_active_tenant


@receiver(form_access_denied)
def _on_form_access_denied(action_name, layer, reason, request, **_):
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

## Further reading

- [`next/static/backends.py`](../../next/static/backends.py) — the `StaticBackend` ABC with the `request=` kwarg.
- [`next/static/manager.py`](../../next/static/manager.py) — the `StaticManager.inject` call site that threads `request`.
- [`next/urls/backends.py`](../../next/urls/backends.py) — the `FileRouterBackend.DIRS` handling that makes `root_pages/` work.
- [`next/components/backends.py`](../../next/components/backends.py) — the matching `FileComponentsBackend.DIRS` handling for `root_blocks/`.
- [`next/deps/providers.py`](../../next/deps/providers.py) — the `RegisteredParameterProvider` ABC used by `TenantProvider`.
- [`next/pages/registry.py`](../../next/pages/registry.py) — the `inherit_context` walk that lifts `tenant` to every descendant page.
- [`docs/content/topics/static-assets/backends.rst`](../../docs/content/topics/static-assets/backends.rst) — the request-aware output section that this example anchors.
- [`docs/content/topics/dependency-injection.rst`](../../docs/content/topics/dependency-injection.rst) — the request-scoped provider pattern.
- [`docs/content/howto/enforce-object-level-permissions.rst`](../../docs/content/howto/enforce-object-level-permissions.rst) — the `check_permissions` and `has_object_permission` hooks used in section 6.
- [`docs/content/topics/forms/signals.rst`](../../docs/content/topics/forms/signals.rst) — the `form_access_denied` payload contract.
