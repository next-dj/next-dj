# next-dj examples

Each folder below is a self-contained Django project that runs on SQLite and Django's in-process `LocMemCache`. No Docker, no Node, no external services. Every example overrides `PAGES_DIR` and `COMPONENTS_DIR` in `NEXT_FRAMEWORK` to demonstrate that the naming is user-controlled.

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
| [`kanban/`](kanban/) | Custom `StaticBackend` that registers a `.jsx` kind through the public `KindRegistry`, multi-level `@context(serialize=True)` with `DeepMergePolicy`, `HashContentDedup` on co-located CSS, composite components with React JSX |

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
* Co-located CSS/JS next to the `page.py`, `component.py`, or `layout.djx` they belong to. The `{% collect_styles %}` / `{% collect_scripts %}` tags place them in the rendered HTML, with deduplication.
* E2E tests driven by `next.testing`: `eager_load_pages`, `reset_registries`, `NextClient`.
* Forms return `HttpResponseRedirect` on success.
