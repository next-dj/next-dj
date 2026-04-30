# Multi-tenant notes

A workspace for two independent tenants (Acme and Globex) that share the same
Django project, the same page tree, and the same static pipeline. Each
request is scoped to one tenant, the page tree resolves notes through that
tenant, the static pipeline rewrites every asset URL with a per-tenant
prefix, and the chrome reads its accent color from a request-derived CSS
variable.

## What you will see

| URL | Description |
|-----|-------------|
| `/` | Workspace landing for the active tenant. Welcome card plus the five most recent notes. |
| `/notes/` | All notes that belong to the active tenant, rendered as `note_card` composites. |
| `/notes/<id>/edit/` | Note editor with title and body inputs and a live `markdown_preview` pane. |

Two tenants ship with the example via a data migration:

| slug | name | accent |
|------|------|--------|
| `acme` | Acme Industries | `#2563eb` (blue) |
| `globex` | Globex Corporation | `#16a34a` (green) |

The header pill carries the tenant name, the accent strip and accent text use
the CSS variable surfaced by the `tenant_theme` context processor, and every
`<link>` and `<script>` URL is prefixed with `/_t/acme/` or `/_t/globex/`.

## Framework features showcased

- **Middleware → DI**. `TenantMiddleware` resolves the active tenant from
  the `X-Tenant` header, attaches it to `request.tenant`, and a
  `RegisteredParameterProvider` named `TenantProvider` exposes it as the
  typed `DTenant` parameter to every page and form action.
- **Custom static backend**. `TenantPrefixStaticBackend` subclasses
  `StaticFilesBackend` and rewrites every collected asset URL with the
  `/_t/<slug>/` prefix using the `request=` hook on
  `render_link_tag`/`render_script_tag`.
- **Shared root pages and root blocks**. The root layout
  ([`root_pages/layout.djx`](root_pages/layout.djx)) and global components
  ([`root_blocks/header/`](root_blocks/header/),
  [`root_blocks/footer/`](root_blocks/footer/)) are wired through the
  `DIRS` section of the page and component backends so the `notes` app does
  not own its HTML shell.
- **Inherit context**. `@context("tenant", inherit_context=True)` in
  [`notes/workspaces/page.py`](notes/workspaces/page.py) lifts the active
  tenant into every descendant page without re-resolving it.
- **Composite component inside a form**. The note edit page renders the
  `markdown_preview` composite next to the body textarea so live previews
  do not need a JSON endpoint.

## How to run

```bash
cd examples/multi-tenant
uv run python manage.py migrate
uv run python manage.py runserver     # http://127.0.0.1:8000/
uv run pytest
```

The migration step seeds two tenants and three demo notes through
[`notes/migrations/0002_demo_data.py`](notes/migrations/0002_demo_data.py).

There are two ways to drive the app:

- **Browser (DEBUG only).** Open
  `http://127.0.0.1:8000/notes/?tenant=acme`. The middleware sets a
  `next_tenant` cookie and redirects to a clean URL. Subsequent navigation
  reuses the cookie. Switch tenants with `?tenant=globex`. The query
  parameter and cookie path are guarded by `settings.DEBUG=True` and exist
  only to make the demo viewable without a header-injecting browser
  extension.
- **API / production.** Send the `X-Tenant` header explicitly:

  ```bash
  curl -H 'X-Tenant: acme' http://127.0.0.1:8000/notes/
  curl -H 'X-Tenant: globex' http://127.0.0.1:8000/notes/
  ```

  The query and cookie fallbacks are disabled outside `DEBUG`. A request
  without the header returns `400 Missing X-Tenant header.`.

Tailwind loads via the Play CDN in
[`root_pages/layout.djx`](root_pages/layout.djx). No Node, no build step.

## Key ideas

### 1. The tenant resolution chain

The chain has three links:

1. [`notes/middleware.py`](notes/middleware.py) parses `X-Tenant` and looks
   up the matching `Tenant` row. Missing slug → `400`. Unknown slug →
   `404`. Match → `request.tenant = tenant`.
2. [`notes/providers.py`](notes/providers.py) defines `DTenant` (a
   `DDependencyBase` marker) and `TenantProvider`, a
   `RegisteredParameterProvider`. The provider matches when
   `param.annotation is DTenant` and `request.tenant` is set. `apps.py`
   imports the module on startup so the auto-registry picks it up.
3. Pages and form actions request the tenant by name and type:

   ```python
   @context("notes")
   def notes(active_tenant: DTenant) -> list[Note]:
       return list(Note.objects.filter(tenant=active_tenant))
   ```

   The framework injects the `Tenant` instance directly. Page modules in
   the example do not start with `from __future__ import annotations`,
   because the DI resolver compares parameter annotations by identity.

### 2. Per-tenant static URL prefix

The custom backend lives in [`notes/backends.py`](notes/backends.py). It
overrides only the `render_*_tag` methods of `StaticFilesBackend`. The
`request` keyword argument is the hook that core threads through
`StaticManager.inject(...)`. For absolute URLs (CDN strings) the helper
falls back to the unmodified URL.

```python
class TenantPrefixStaticBackend(StaticFilesBackend):
    def render_link_tag(self, url, *, request=None):
        return super().render_link_tag(_prefixed(url, request))

    def render_script_tag(self, url, *, request=None):
        return super().render_script_tag(_prefixed(url, request))
```

The settings entry is a single line:

```python
"DEFAULT_STATIC_BACKENDS": [
    {"BACKEND": "notes.backends.TenantPrefixStaticBackend"},
]
```

The `next.static` collector caches deduplicated URLs once. The
`render_*_tag` hook lets you decorate URLs at injection time without
forking that cache.

### 3. Shared root layout via `DIRS`

The `notes` Django app does not own its HTML shell. The root template lives
under [`root_pages/layout.djx`](root_pages/layout.djx) and the global
components live under [`root_blocks/header/`](root_blocks/header/) and
[`root_blocks/footer/`](root_blocks/footer/). The page and component
backends pick them up through the `DIRS` setting:

```python
"DEFAULT_PAGE_BACKENDS": [
    {
        "BACKEND": "next.urls.FileRouterBackend",
        "APP_DIRS": True,
        "DIRS": [BASE_DIR / "root_pages"],
        "PAGES_DIR": "workspaces",
        ...
    },
],
"DEFAULT_COMPONENT_BACKENDS": [
    {
        "BACKEND": "next.components.FileComponentsBackend",
        "DIRS": [BASE_DIR / "root_blocks"],
        "COMPONENTS_DIR": "_blocks",
    },
],
```

This is the canonical way to share chrome across multiple Django apps. See
[`docs/content/guide/project-layout.rst`](../../docs/content/guide/project-layout.rst)
for the broader pattern.

### 4. Inherit context for the active tenant

[`notes/workspaces/page.py`](notes/workspaces/page.py) registers two
`@context(..., inherit_context=True)` callables:

```python
@context("tenant", inherit_context=True)
def tenant(active_tenant: DTenant) -> Tenant:
    return active_tenant


@context("recent_notes", inherit_context=True)
def recent_notes(active_tenant: DTenant) -> list[Note]:
    return list(Note.objects.filter(tenant=active_tenant)[:5])
```

Every descendant page (`/notes/`, `/notes/<id>/edit/`) sees `tenant` in its
template context. The `notes/template.djx` and the `note_card` component
read it without re-resolving the tenant, and the URL kwargs handler in
`note_edit` form action gets it as a typed parameter through DI.

### 5. The note edit form with a composite preview

[`notes/workspaces/notes/[int:id]/edit/page.py`](notes/workspaces/notes/[int:id]/edit/page.py)
defines `NoteEditForm` with hidden `note_id`, plain `title`, and free-form
`body` fields. The handler ignores the URL kwarg `id` and trusts only
`form.cleaned_data["note_id"]`, then routes the lookup through
`get_object_or_404(Note, pk=note_id, tenant=active_tenant)` so a tenant
posting another tenant's note id receives a `404`.

The body textarea is rendered side by side with the
`markdown_preview` composite ([`_blocks/markdown_preview/`](notes/workspaces/notes/_blocks/markdown_preview/)).
The composite imports the `markdown` package and renders the body through
`mark_safe` after letting the renderer escape raw HTML.

## Tests

The example ships unit and end-to-end tests at
[`tests/test_unit.py`](tests/test_unit.py) and
[`tests/test_e2e.py`](tests/test_e2e.py). Coverage stays above 90% on
`notes/`.

End-to-end coverage:

- `TestTenantContract` asserts that the production header path is the only
  way to reach the page tree when `DEBUG=False`.
- `TestTenantTheme` checks that the right CSS variable lands in the
  rendered HTML for each tenant.
- `TestTenantPrefixStatic` asserts that every `<link>` and `<script>` URL
  carries the `/_t/<slug>/` prefix.
- `TestRootBlocks` confirms that the shared header and footer come from
  `root_blocks/`.
- `TestNoteEditForm` exercises the happy-path edit and the cross-tenant
  isolation case.
- `TestDebugAffordance` covers the DEBUG-only `?tenant=` and `next_tenant`
  cookie path, plus the production short-circuit.

Unit tests cover the middleware error branches, the provider matching
logic, the context processor, the static backend URL rewriting, and both
composite components.

## Forward-compat

- **Suspense / partial renders.** `tenant` is exposed via `@context`, so a
  future native suspense API can swap the inherit step without touching
  templates.
- **Native context inheritance.** `inherit_context=True` is the explicit
  marker today. The inherit semantics will switch to the framework default
  once parent-page policies land.
- **Per-tenant CDN.** The static backend already routes through Django
  `staticfiles_storage`. Pointing `STATICFILES_STORAGE` at S3 or a CDN
  storage automatically composes with the `/_t/<slug>/` prefix.
- **Native React bridge.** None of the React patterns from `kanban` or
  `live-polls` are required here, but the page tree is React-ready: the
  `note_card` composite renders straight HTML today and can be swapped for
  a JSX equivalent without changing routes or DI plumbing.

## Further reading

- [`next/static/backends.py`](../../next/static/backends.py) — the
  `StaticBackend` ABC with the `request=` kwarg.
- [`next/static/manager.py`](../../next/static/manager.py) — the
  `StaticManager.inject` call site that threads `request`.
- [`next/urls/backends.py`](../../next/urls/backends.py) — the
  `FileRouterBackend.DIRS` handling that makes `root_pages/` work.
- [`next/components/backends.py`](../../next/components/backends.py) — the
  matching `FileComponentsBackend.DIRS` handling for `root_blocks/`.
- [`next/deps/providers.py`](../../next/deps/providers.py) — the
  `RegisteredParameterProvider` ABC used by `TenantProvider`.
- [`next/pages/registry.py`](../../next/pages/registry.py) — the
  `inherit_context` walk that lifts `tenant` to every descendant page.
- [`docs/content/guide/static-assets.rst`](../../docs/content/guide/static-assets.rst#request-aware-backends)
  — the request-aware backend section that this example anchors.
- [`docs/content/guide/dependency-injection.rst`](../../docs/content/guide/dependency-injection.rst)
  — the request-scoped provider pattern.
