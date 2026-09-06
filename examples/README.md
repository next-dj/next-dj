# next-dj examples

Each folder below is a self-contained Django project that runs on SQLite and Django's in-process `LocMemCache`. No Docker and no external services are required. Only [`kanban/`](kanban/) and [`live-polls/`](live-polls/) carry a Vite toolchain and need Node, every other example loads Tailwind from the Play CDN with no build step. Every example overrides `PAGES_DIR` and `COMPONENTS_DIR` in `NEXT_FRAMEWORK` to demonstrate that the naming is user-controlled. Each example also registers a project-level page root through `PAGE_BACKENDS["DIRS"]` under a name of its own, and that root owns the shared HTML envelope sitting outside the per-app page tree.

## Shared UI kit

- [`_shared/`](_shared/) — shadcn-inspired component palette every example consumes through `COMPONENT_BACKENDS["DIRS"]`. Tokens (`hsl(var(--background))`, `hsl(var(--primary))`, …) live in [`_shared/static/shared/css/tokens.css`](_shared/static/shared/css/tokens.css). Components (button, card, badge, input, alert, table primitives, nav_link, page_header, app_shell, dialog, dropdown, …) live in [`_shared/_components/`](_shared/_components/) and render through the same `{% component "name" %}` tag examples already use. See [`_shared/README.md`](_shared/README.md) for the full inventory and wiring contract.

## Starter scaffold

- [`_template/`](_template/) — the skeleton every example is copied from. It runs and its test passes, but demonstrates nothing on its own. [`_template/README.md`](_template/README.md) carries the rename checklist, because each rename touches more than one file.

## Examples

Each example is a complete mini-product with e2e tests and a dedicated README. Read [`shortener/`](shortener/) first, it is written as a tour of the surface you use most and the later examples build on it rather than repeat it. The table is ordered roughly by how much it assumes.

| Folder | Focus |
| --- | --- |
| [`shortener/`](shortener/) | File router + DI providers + LocMemCache + management command + declarative `Meta.success_url` / `Meta.success_message` form contract |
| [`markdown-blog/`](markdown-blog/) | Markdown posts, nested layouts, `@context(serialize=True)`, context processor, co-located `component.js` |
| [`feature-flags/`](feature-flags/) | Composite `feature_guard`, signal receivers, cache invalidation |
| [`audit-forms/`](audit-forms/) | Custom `FormActionBackend`, `action_dispatched` and `form_validation_failed` signals, dual audit channels |
| [`search-catalog/`](search-catalog/) | `DQuery[T]`, faceted filters, three-level nested layouts, `inherit_context=True`, cached search |
| [`wiki/`](wiki/) | `HybridRouterBackend`, `router_manager.reload()` on signal, `DArticle` DI provider, forms with live Markdown preview |
| [`multi-tenant/`](multi-tenant/) | `TenantMiddleware` resolving `X-Tenant`, request-aware `TenantPrefixStaticBackend` rewriting co-located asset URLs per tenant, project-shared `root_blocks/header` and `root_blocks/footer` via `COMPONENT_BACKENDS["DIRS"]`, per-tenant accent driven by a context processor |
| [`kanban/`](kanban/) | Custom `StaticBackend` that registers a `.jsx` kind through the public `KindRegistry`, multi-level `@context(serialize=True)` with `DeepMergePolicy`, `HashContentDedup` on co-located CSS, composite components with React JSX |
| [`live-polls/`](live-polls/) | Server-Sent Events stream from a `threading.Condition` broker with per-poll monotonic revisions, signal-driven fan-out using the bound form on `action_dispatched`, locally bundled Vue 3 SFC subscribing through `EventSource`, custom `.vue` asset kind, three-level nested layouts with `inherit_context=True` |
| [`observability/`](observability/) | Every signal group wired through one receiver each, custom `ComponentsBackend` and `DedupStrategy`, `JsContextSerializer` swapped both globally and per-decorator on `live_stats`, React sparkline via CDN-loaded Babel-standalone, nested layout with filter form action |
| [`admin/`](admin/) | Full Django admin UI on next.dj: shadcn-style templates over `ModelAdmin.get_changelist_instance()`/`get_form()`/`get_inline_instances()`/`get_actions()`, request-aware form factories via `@action(form_class=callable)`, declarative `login_required=True` guards plus `ModelAdmin` permission checks on every mutating action, `{% action_url %}` in a hand-crafted form, a two-layer layout whose inner shell branches between admin chrome and auth chrome, middleware guard for `/admin/` pages, LogEntry history |

## Running any example

```bash
cd examples/<name>
uv run python manage.py migrate
uv run python manage.py seed_demo     # only where the example ships a demo dataset
uv run python manage.py runserver
uv run pytest
```

Tailwind is loaded via the Play CDN (`https://cdn.tailwindcss.com`) through the shared `page_head` component that every root layout calls. The two Vite examples add an `npm install` plus a dev server or a build on top, documented in their own READMEs.

## Conventions every example follows

- One custom `PAGES_DIR` (`routes`, `screens`, `panels`, …) and one custom components directory (`_widgets`, `_parts`, `_chunks`, …).
- One project-level page root listed in `PAGE_BACKENDS["DIRS"]` (e.g. `host/`, `frame/`, `shell/`, `studio/`). The file router walks it alongside the per-app `PAGES_DIR`, and its `layout.djx` becomes the outermost wrapper around every page. The [`multi-tenant`](multi-tenant/) example goes one step further and also drops project-shared components inside this root (`root_blocks/header`, `root_blocks/footer`). The [`markdown-blog`](markdown-blog/) example shows the same trick with a `site/_parts/site_footer` registered through `COMPONENT_BACKENDS["DIRS"]`.
- `PARTIAL_BACKENDS` pins an explicit asset `VERSION` through `next.conf.extend_default_backend`. The default `"manifest"` sentinel derives the version stamp a partial response carries from a hashed staticfiles manifest, so shipping new assets asks open clients to reload instead of patching against stale JavaScript. An example serves its assets straight off disk with no such manifest, which would leave the guard permanently silent, and `manage.py check` reports that as `next.W069`. Bump the pinned tag when the assets change.
- Co-located CSS/JS next to the `page.py`, `component.py`, or `layout.djx` they belong to. The `{% collect_styles %}` / `{% collect_scripts %}` tags place them in the rendered HTML, with deduplication.
- Demo data lives in an app-level `demo.py` behind a `seed_demo` management command, never in a `RunPython` migration. Migrations stay schema-only, so a fresh test database starts empty and a test asks for the dataset through the example's `demo_data` fixture. Six examples ship one: [`admin`](admin/), [`feature-flags`](feature-flags/), [`kanban`](kanban/), [`live-polls`](live-polls/), [`multi-tenant`](multi-tenant/), and [`search-catalog`](search-catalog/).
- E2E tests driven by `next.testing`. Every example opts into the framework's pytest plugin from its own `pytest.ini` (`addopts = ... -p next.testing.plugin`) rather than copying a conftest: `next_pages` names the page directories imported once per session, `next_clear_cache` resets the `LocMemCache` before each test, and the `next_client` fixture is the client that speaks the partial protocol. See [`_template/pytest.ini`](_template/pytest.ini).
- Forms answer a successful submit in one of two ways. A full-cycle form redirects, either declaratively through `Meta.success_url` (plus an optional `Meta.success_message` flash, see [`shortener`](shortener/)) or by returning `HttpResponseRedirect` from `on_valid` when the target depends on the submission. A form that patches the page instead returns a `Patches(request)` envelope, which falls back to a redirect on its own when no runtime is present, so the no-JavaScript path stays intact either way. The [`admin`](admin/) example shows the access side of the same surface: declarative `login_required=True` guards on every mutating action.
- Partial rendering runs through most of the catalog rather than sitting in one example. `{% zone %}` marks the region a patch addresses and `Patches(request)` builds the envelope that fills it: a prepend into a list in [`shortener`](shortener/), a modal wizard in [`audit-forms`](audit-forms/), a polling zone plus a project-defined patch verb in [`observability`](observability/), auto-submit filters and infinite scroll in [`search-catalog`](search-catalog/), an SSE fan-out in [`live-polls`](live-polls/), and keyed inline row forms in [`admin`](admin/).
