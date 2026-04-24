# next-dj examples

Each folder below is a self-contained Django project that runs on SQLite and Django's in-process `LocMemCache`. No Docker, no Node, no external services. Every example overrides `PAGES_DIR` and `COMPONENTS_DIR` in `NEXT_FRAMEWORK` to demonstrate that the naming is user-controlled.

## Starter scaffold

* [`_template/`](_template/) — empty skeleton. Copy, rename `myapp`, and fill in.

## Examples

The example set is being rebuilt to showcase specific framework features in isolation. Each example is a complete mini-product with e2e tests and a dedicated README.

| # | Folder | Focus |
|---|--------|-------|
| 1 | [`shortener/`](shortener/) | File router + DI providers + LocMemCache + management command |
| 2 | [`markdown-blog/`](markdown-blog/) | Custom `TemplateLoader`, `@context(serialize=True)`, nested layouts |
| 4 | [`feature-flags/`](feature-flags/) | Composite `feature_guard`, signal receivers, cache invalidation |

More examples (3, 5, 6, 7, 8, 9, 10) are being added in subsequent PRs.

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
* Forms return `HttpResponseRedirect` on success so partial-rerender support can be added later without touching the request/response contract.
