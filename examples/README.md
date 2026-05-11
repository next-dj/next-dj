# next-dj examples

Each folder below is a self-contained Django project that runs on SQLite and Django's in-process `LocMemCache`. No Docker, no Node, no external services. Every example overrides `PAGES_DIR` and `COMPONENTS_DIR` in `NEXT_FRAMEWORK` to demonstrate that the naming is user-controlled, and lists a project-level page root through `DEFAULT_PAGE_BACKENDS["DIRS"]` (named differently in each project — `chrome/`, `host/`, `site/`, `frame/`, `shell/`, `portal/`, `instrument/`, `marketplace/`, `cockpit/`, `studio/`, `root_pages/`) that owns the shared HTML envelope sitting outside the per-app page tree.

## Shared UI kit

* [`_shared/`](_shared/) — shadcn-inspired component palette every example consumes through `DEFAULT_COMPONENT_BACKENDS["DIRS"]`. Tokens (`hsl(var(--background))`, `hsl(var(--primary))`, …) live in [`_shared/static/shared/css/tokens.css`](_shared/static/shared/css/tokens.css). Components (button, card, badge, input, alert, table primitives, nav_link, page_header, app_shell, dialog, dropdown, …) live in [`_shared/_components/`](_shared/_components/) and render through the same `{% component "name" %}` tag examples already use. See [`_shared/README.md`](_shared/README.md) for the full inventory and wiring contract.

## Starter scaffold

* [`_template/`](_template/) — empty skeleton. Copy, rename `myapp`, and fill in.

## Examples

Each example is a complete mini-product with e2e tests and a dedicated README.

| Folder | Focus |
|--------|-------|
| [`shortener/`](shortener/) | File router + DI providers + LocMemCache + management command |
| [`markdown-blog/`](markdown-blog/) | Markdown posts, nested layouts, `@context(serialize=True)`, context processor, co-located `component.js` |
| [`feature-flags/`](feature-flags/) | Composite `feature_guard`, signal receivers, cache invalidation |
| [`audit-forms/`](audit-forms/) | Custom `FormActionBackend`, `action_dispatched` and `form_validation_failed` signals, dual audit channels |
| [`search-catalog/`](search-catalog/) | `DQuery[T]`, faceted filters, three-level nested layouts, `inherit_context=True`, cached search |
| [`wiki/`](wiki/) | `HybridRouterBackend`, `router_manager.reload()` on signal, `DArticle` DI provider, forms with live Markdown preview |
| [`multi-tenant/`](multi-tenant/) | `TenantMiddleware` resolving `X-Tenant`, request-aware `TenantPrefixStaticBackend` rewriting co-located asset URLs per tenant, project-shared `root_blocks/header` and `root_blocks/footer` via `DEFAULT_COMPONENT_BACKENDS["DIRS"]`, per-tenant accent driven by a context processor |
| [`kanban/`](kanban/) | Custom `StaticBackend` that registers a `.jsx` kind through the public `KindRegistry`, multi-level `@context(serialize=True)` with `DeepMergePolicy`, `HashContentDedup` on co-located CSS, composite components with React JSX |
| [`live-polls/`](live-polls/) | Server-Sent Events stream from a `threading.Condition` broker with per-poll monotonic revisions, signal-driven fan-out using the bound form on `action_dispatched`, locally bundled Vue 3 SFC subscribing through `EventSource`, custom `.vue` asset kind, three-level nested layouts with `inherit_context=True` |
| [`observability/`](observability/) | Every signal group wired through one receiver each, custom `ComponentsBackend` and `DedupStrategy`, `JsContextSerializer` swapped both globally and per-decorator on `live_stats`, React sparkline via CDN-loaded Babel-standalone, nested layout with filter form action |

## Running any example

```bash
cd examples/<name>
uv run python manage.py migrate
uv run python manage.py runserver
uv run pytest
```

Tailwind is loaded via the Play CDN (`https://cdn.tailwindcss.com`) from the root layout. No build step is required.

## Conventions every example follows

* One custom `PAGES_DIR` (`routes`, `screens`, `panels`, …) and one custom components directory (`_widgets`, `_parts`, `_chunks`, …).
* One project-level page root listed in `DEFAULT_PAGE_BACKENDS["DIRS"]` (e.g. `host/`, `frame/`, `shell/`, `studio/`). The file router walks it alongside the per-app `PAGES_DIR`, and its `layout.djx` becomes the outermost wrapper around every page. The [`multi-tenant`](multi-tenant/) example goes one step further and also drops project-shared components inside this root (`root_blocks/header`, `root_blocks/footer`); [`markdown-blog`](markdown-blog/) shows the same trick with a `site/_parts/site_footer` registered through `DEFAULT_COMPONENT_BACKENDS["DIRS"]`.
* Co-located CSS/JS next to the `page.py`, `component.py`, or `layout.djx` they belong to. The `{% collect_styles %}` / `{% collect_scripts %}` tags place them in the rendered HTML, with deduplication.
* E2E tests driven by `next.testing`: `eager_load_pages`, `reset_registries`, `NextClient`.
* Forms return `HttpResponseRedirect` on success.
